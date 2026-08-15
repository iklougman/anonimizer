from __future__ import annotations

import uuid
from functools import lru_cache

from app.config import get_settings
from app.privacy_gateway.detectors.custom_recognizers import CustomRecognizers
from app.privacy_gateway.detectors.presidio_detector import PresidioDetector
from app.privacy_gateway.detectors.regex_detector import RegexDetector
from app.privacy_gateway.detectors.stack import DetectorStack
from app.privacy_gateway.output_guard.guard import (
    LeakageDetectedError,
    OutputGuard,
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
from app.privacy_gateway.token_vault.vault import TokenVault

__all__ = [
    "HighRiskMessageError",
    "LeakageDetectedError",
    "LowConfidenceSpanError",
    "Pipeline",
    "UnresolvedTokenError",
    "deanonymize",
    "get_pipeline",
    "sanitize",
]


class Pipeline:
    def __init__(
        self,
        detector_stack: DetectorStack,
        risk_scorer: RiskScorer,
        pseudonymizer: Pseudonymizer,
        output_guard: OutputGuard,
    ) -> None:
        self._detector_stack = detector_stack
        self._risk_scorer = risk_scorer
        self._pseudonymizer = pseudonymizer
        self._output_guard = output_guard

    def sanitize(
        self, tenant_id: uuid.UUID, conversation_id: uuid.UUID, text: str
    ) -> str:
        """Design spec §4: runs all three detector layers, risk-scores every span,
        raises on HIGH-risk or low-confidence, else returns the fully pseudonymized
        string ready to send to an LLM.
        """
        spans = self._detector_stack.detect(text)
        assessment = self._risk_scorer.score(text, spans)
        return self._pseudonymizer.apply(
            tenant_id, conversation_id, text, assessment.tokenize
        )

    def deanonymize(
        self, tenant_id: uuid.UUID, conversation_id: uuid.UUID, llm_output: str
    ) -> str:
        """Design spec §5: leakage scan, then authorization-checked token resolution."""
        return self._output_guard.restore(tenant_id, conversation_id, llm_output)


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
    )


def sanitize(tenant_id: uuid.UUID, conversation_id: uuid.UUID, text: str) -> str:
    return get_pipeline().sanitize(tenant_id, conversation_id, text)


def deanonymize(
    tenant_id: uuid.UUID, conversation_id: uuid.UUID, llm_output: str
) -> str:
    return get_pipeline().deanonymize(tenant_id, conversation_id, llm_output)
