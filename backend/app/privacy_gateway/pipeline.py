from __future__ import annotations

import logging
import uuid
from collections import Counter
from functools import lru_cache

from app.config import get_settings
from app.privacy_gateway.detectors.custom_recognizers import CustomRecognizers
from app.privacy_gateway.detectors.presidio_detector import PresidioDetector
from app.privacy_gateway.detectors.regex_detector import RegexDetector
from app.privacy_gateway.detectors.stack import DetectorStack
from app.privacy_gateway.output_guard.guard import (
    LeakageDetectedError,
    OutputGuard,
    ResidualPIIError,
    UnresolvedTokenError,
)
from app.privacy_gateway.pseudonymization.pseudonymizer import Pseudonymizer
from app.privacy_gateway.risk_scoring.reference_data import (
    get_hospital_names,
    get_rare_disease_names,
)
from app.privacy_gateway.risk_scoring.scorer import (
    HighRiskMessageError,
    LowConfidenceSpanError,
    RiskScorer,
)
from app.privacy_gateway.token_vault.key_provider import FileSecretKeyProvider
from app.privacy_gateway.token_vault.vault import ScopeType, TokenVault

__all__ = [
    "HighRiskMessageError",
    "LeakageDetectedError",
    "LowConfidenceSpanError",
    "Pipeline",
    "ResidualPIIError",
    "UnresolvedTokenError",
    "deanonymize",
    "get_pipeline",
    "sanitize",
]

logger = logging.getLogger(__name__)


class Pipeline:
    def __init__(
        self,
        detector_stack: DetectorStack,
        risk_scorer: RiskScorer,
        pseudonymizer: Pseudonymizer,
        output_guard: OutputGuard,
        guard_enabled: bool = True,
    ) -> None:
        self._detector_stack = detector_stack
        self._risk_scorer = risk_scorer
        self._pseudonymizer = pseudonymizer
        self._output_guard = output_guard
        self._guard_enabled = guard_enabled
        if not guard_enabled:
            logger.warning(
                "pipeline.init output_guard DISABLED via OUTPUT_GUARD_ENABLED=false: "
                "deanonymize will return raw LLM completions with the post-LLM leakage "
                "scan bypassed. This is a debug override and must not be used outside "
                "development -- it may return PII the model hallucinated."
            )

    def sanitize(
        self, tenant_id: uuid.UUID, scope_type: ScopeType, scope_id: uuid.UUID, text: str
    ) -> str:
        """Design spec §4: runs all three detector layers, risk-scores every span,
        raises on HIGH-risk or low-confidence, else returns the fully pseudonymized
        string ready to send to an LLM.

        Every step logs its result at INFO, and every fail-closed rejection logs at
        WARNING before the exception propagates -- entity types, counts, positions,
        and category labels only, never the matched text itself, so the log stream
        stays within the same privacy boundary this pipeline enforces on the LLM.
        """
        spans = self._detector_stack.detect(text)
        span_counts = dict(Counter(span.entity_type for span in spans))
        logger.info(
            "sanitize.detect tenant_id=%s scope_type=%s scope_id=%s spans=%d types=%s",
            tenant_id, scope_type, scope_id, len(spans), span_counts,
        )

        try:
            assessment = self._risk_scorer.score(text, spans)
        except (LowConfidenceSpanError, HighRiskMessageError) as exc:
            logger.warning(
                "sanitize.reject tenant_id=%s scope_type=%s scope_id=%s reason=%s: %s",
                tenant_id, scope_type, scope_id, type(exc).__name__, exc,
            )
            raise

        logger.info(
            "sanitize.risk_assessment tenant_id=%s scope_type=%s scope_id=%s "
            "quasi_identifier_categories=%s rare_disease_matches=%d tokens_to_issue=%d",
            tenant_id,
            scope_type,
            scope_id,
            sorted(assessment.quasi_identifier_categories),
            len(assessment.rare_diseases),
            len(assessment.tokenize),
        )

        result = self._pseudonymizer.apply(
            tenant_id, scope_type, scope_id, text, assessment.tokenize
        )

        # Pre-send check: verify sanitize()'s own output before it ever reaches an
        # LLM, using the identical mask-then-rescan step the output guard runs on
        # LLM replies. This catches a pseudonymization bug (a detected span that
        # didn't get substituted), not a detector blind spot -- an entity layer 1-3
        # never recognized here was equally invisible to the scan two lines above.
        try:
            self._output_guard.assert_no_raw_pii(result)
        except ResidualPIIError as exc:
            logger.warning(
                "sanitize.residual_pii tenant_id=%s scope_type=%s scope_id=%s reason=%s: %s",
                tenant_id, scope_type, scope_id, type(exc).__name__, exc,
            )
            raise

        logger.info(
            "sanitize.pseudonymize tenant_id=%s scope_type=%s scope_id=%s tokens_issued=%d -> PASS",
            tenant_id, scope_type, scope_id, len(assessment.tokenize),
        )
        return result

    def deanonymize(
        self, tenant_id: uuid.UUID, scope_type: ScopeType, scope_id: uuid.UUID, llm_output: str
    ) -> str:
        """Design spec §5: leakage scan, then authorization-checked token resolution.

        When OUTPUT_GUARD_ENABLED=false (debug only), the leakage scan is bypassed
        via OutputGuard.restore_unchecked so the raw LLM completion (tokens
        resolved, no leakage re-scan) is returned for inspection.
        UnresolvedTokenError still raises -- that is a correctness failure, not a
        privacy gate.
        """
        try:
            if self._guard_enabled:
                result = self._output_guard.restore(tenant_id, scope_type, scope_id, llm_output)
            else:
                logger.warning(
                    "deanonymize.UNGUARDED tenant_id=%s scope_type=%s scope_id=%s "
                    "output_guard disabled by config; raw LLM output returned",
                    tenant_id, scope_type, scope_id,
                )
                result = self._output_guard.restore_unchecked(
                    tenant_id, scope_type, scope_id, llm_output
                )
        except UnresolvedTokenError as exc:
            logger.warning(
                "deanonymize.reject tenant_id=%s scope_type=%s scope_id=%s reason=%s: %s",
                tenant_id, scope_type, scope_id, type(exc).__name__, exc,
            )
            raise
        except LeakageDetectedError as exc:
            logger.warning(
                "deanonymize.reject tenant_id=%s scope_type=%s scope_id=%s reason=%s: %s",
                tenant_id, scope_type, scope_id, type(exc).__name__, exc,
            )
            raise

        logger.info(
            "deanonymize.restore tenant_id=%s scope_type=%s scope_id=%s -> PASS",
            tenant_id, scope_type, scope_id,
        )
        return result


@lru_cache(maxsize=1)
def get_pipeline() -> Pipeline:
    """Build the process-wide pipeline once.

    Design spec §6: the ORDO OWL and Krankenhausverzeichnis xlsx are parsed here,
    once, into in-memory sets — never per request.
    """
    detector_stack = DetectorStack(
        RegexDetector(), PresidioDetector(), CustomRecognizers(get_hospital_names())
    )
    vault = TokenVault(FileSecretKeyProvider(get_settings().master_key_path))
    return Pipeline(
        detector_stack=detector_stack,
        risk_scorer=RiskScorer(get_rare_disease_names()),
        pseudonymizer=Pseudonymizer(vault),
        output_guard=OutputGuard(detector_stack, vault),
        guard_enabled=get_settings().output_guard_enabled,
    )


def sanitize(tenant_id: uuid.UUID, scope_type: ScopeType, scope_id: uuid.UUID, text: str) -> str:
    return get_pipeline().sanitize(tenant_id, scope_type, scope_id, text)


def deanonymize(
    tenant_id: uuid.UUID, scope_type: ScopeType, scope_id: uuid.UUID, llm_output: str
) -> str:
    return get_pipeline().deanonymize(tenant_id, scope_type, scope_id, llm_output)
