from __future__ import annotations

import re
import uuid

from app.privacy_gateway.detectors.base import Span
from app.privacy_gateway.detectors.stack import DetectorStack
from app.privacy_gateway.token_vault.vault import ScopeType, TokenVault

# ADR-0009's token format: entity type + secrets.token_hex(5).upper(). The design
# spec's `[TYPE_XXXXX]` notation is shorthand for this shape — there are no literal
# square brackets in a token.
TOKEN_PATTERN = re.compile(r"\b[A-Z][A-Z_]*_[0-9A-F]{10}\b")


class LeakageDetectedError(Exception):
    """The LLM's raw output contains detected PII that is not a legitimate token.

    Design spec §5 step 1: fail-closed — raise, do not return partial output. No
    audit_events row is written here; audit_events repository access is out of scope
    per the data-model plan, so this typed exception is what the caller logs.

    Carries `entity_types` (one per leaked span, in detection order) rather than
    just the string message, so a caller building an audit trail can record what
    kind of PII leaked without re-parsing the message text.
    """

    def __init__(self, message: str, entity_types: list[str] | None = None) -> None:
        super().__init__(message)
        self.entity_types = entity_types or []


class ResidualPIIError(Exception):
    """A just-pseudonymized string still contains PII outside its own tokens.

    Runs before the text is ever sent to an LLM (Pipeline.sanitize, after
    Pseudonymizer.apply), so unlike LeakageDetectedError this cannot be the LLM
    inventing or leaking anything -- it means sanitize()'s own detect-then-substitute
    step missed or mis-substituted a span. It is a check on this pipeline's own
    output, not a compensation for detector recall: an entity the first detector
    pass never recognized is invisible to this identical second pass too.

    Carries `entity_types` for the same reason LeakageDetectedError does.
    """

    def __init__(self, message: str, entity_types: list[str] | None = None) -> None:
        super().__init__(message)
        self.entity_types = entity_types or []


class UnresolvedTokenError(Exception):
    """A token-shaped substring in the LLM output did not resolve for this
    (tenant_id, conversation_id).

    Design spec §5 step 4: a token the LLM could not have legitimately produced —
    another tenant's, another conversation's, or fabricated by prompt injection.
    Raise rather than returning it opaque or silently dropping it.

    Carries `tokens` (the unresolved token strings) for the same reason
    `LeakageDetectedError` carries `entity_types`.
    """

    def __init__(self, message: str, tokens: list[str] | None = None) -> None:
        super().__init__(message)
        self.tokens = tokens or []


class OutputGuard:
    def __init__(self, detector_stack: DetectorStack, vault: TokenVault) -> None:
        self._detector_stack = detector_stack
        self._vault = vault

    def restore(
        self, tenant_id: uuid.UUID, scope_type: ScopeType, scope_id: uuid.UUID, llm_output: str
    ) -> str:
        token_bounds = _token_bounds(llm_output)

        # Step 1: re-run the full detector stack over the output with every token
        # masked out; anything still detected is raw-looking PII the LLM produced or
        # leaked. Masking (rather than detecting on the raw output and then asking
        # whether each span sits inside a token) is what makes this check stable —
        # see _mask_tokens.
        leaked = self._scan(llm_output, token_bounds)
        if leaked:
            raise LeakageDetectedError(
                "raw-looking PII in LLM output: "
                + ", ".join(
                    f"{span.entity_type} at [{span.start}:{span.end}]" for span in leaked
                )
                + "; the response is rejected rather than partially returned",
                entity_types=[span.entity_type for span in leaked],
            )

        return self._resolve(tenant_id, scope_type, scope_id, llm_output, token_bounds)

    def restore_unchecked(
        self, tenant_id: uuid.UUID, scope_type: ScopeType, scope_id: uuid.UUID, llm_output: str
    ) -> str:
        """Debug path (OUTPUT_GUARD_ENABLED=false): skip the leakage scan but still
        resolve tokens and still fail on unresolved ones. Returns the LLM's raw
        completion with tokens substituted back -- including any real-looking PII
        the model may have hallucinated -- so an operator can inspect what the model
        actually produced. Steps 2-4 of restore() run unchanged; only step 1 is
        bypassed.
        """
        return self._resolve(
            tenant_id, scope_type, scope_id, llm_output, _token_bounds(llm_output)
        )

    def _resolve(
        self,
        tenant_id: uuid.UUID,
        scope_type: ScopeType,
        scope_id: uuid.UUID,
        llm_output: str,
        token_bounds: list[tuple[int, int]],
    ) -> str:
        # Steps 2 and 3: extract the token-shaped substrings and let TokenVault apply
        # the (tenant_id, scope_type, scope_id) authorization scope.
        tokens = [llm_output[start:end] for start, end in token_bounds]
        resolved = self._vault.resolve_tokens(tenant_id, scope_type, scope_id, tokens)

        # Step 4: anything still unresolved is fail-closed.
        unresolved = sorted({token for token in tokens if token not in resolved})
        if unresolved:
            raise UnresolvedTokenError(
                "token(s) not issued for this tenant and conversation: "
                + ", ".join(unresolved)
                + "; the response is rejected rather than returned opaque",
                tokens=unresolved,
            )

        restored = llm_output
        for start, end in reversed(token_bounds):
            restored = restored[:start] + resolved[llm_output[start:end]] + restored[end:]
        return restored

    def assert_no_raw_pii(self, text: str) -> None:
        """Pre-send check: run the identical mask-then-rescan step `restore()` runs
        on LLM output against a just-pseudonymized string instead, before it ever
        leaves the network boundary. Raises ResidualPIIError, not
        LeakageDetectedError -- this text was produced by Pseudonymizer.apply(), not
        an LLM, so a hit here means the pipeline's own substitution missed a span.
        """
        token_bounds = _token_bounds(text)
        leaked = self._scan(text, token_bounds)
        if leaked:
            raise ResidualPIIError(
                "raw PII surviving pseudonymization: "
                + ", ".join(
                    f"{span.entity_type} at [{span.start}:{span.end}]" for span in leaked
                )
                + "; the message is rejected rather than sent to the LLM unverified",
                entity_types=[span.entity_type for span in leaked],
            )

    def _scan(self, text: str, token_bounds: list[tuple[int, int]]) -> list[Span]:
        return self._detector_stack.detect(_mask_tokens(text, token_bounds))


def _token_bounds(text: str) -> list[tuple[int, int]]:
    return [(match.start(), match.end()) for match in TOKEN_PATTERN.finditer(text)]


MASK_CHARACTER = "#"


def _mask_tokens(text: str, token_bounds: list[tuple[int, int]]) -> str:
    """Replace every token with an equal-length run of `#` before the leakage scan.

    A token is a run of random hex glued to an uppercase type name — text no German
    NER model has ever seen. Left in place it does not merely fail to be recognized,
    it actively corrupts the tagging of the *surrounding* prose: `de_core_news_lg`
    tags "Dr. DOCTOR_A9B8661…" as one PERSON, and sweeps whole clauses
    ("HOSPITAL_… von Dr. DOCTOR_…") into a single LOCATION. Because the hex differs
    on every call, which spans it invents differs run to run — measured over the
    golden corpus, 11 of 21 notes intermittently failed their own round-trip that
    way, with no PII having leaked at all.

    Masking removes that input entirely and makes the scan deterministic. Equal
    length keeps every offset in `token_bounds` valid for the restore step below.
    `#` (not whitespace) because it preserves the sentence's token structure: a
    masked run still occupies the slot a word occupied, whereas blanking it out
    leaves a hole that shifts the tagging of neighbouring words ("… erfolgte am
    <hole> nach Hause" makes "Hause" look like a LOCATION).

    This does not weaken the check. Only the tokens are masked; every character the
    LLM wrote outside them is scanned unchanged and in its original context, so real
    PII next to a token — "PATIENT_1234567890 heißt in Wahrheit Lukas Berger" — is
    still detected and still fails closed.
    """
    masked = text
    for start, end in token_bounds:
        masked = masked[:start] + MASK_CHARACTER * (end - start) + masked[end:]
    return masked
