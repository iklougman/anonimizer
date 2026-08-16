import re

import pytest

from app.privacy_gateway.pipeline import (
    HighRiskMessageError,
    LeakageDetectedError,
    UnresolvedTokenError,
)
from tests.privacy_invariants.conftest import load_golden_corpus, new_scope

TOKEN_PATTERN = re.compile(r"[A-Z][A-Z_]*_[0-9A-F]{10}")

CORPUS = load_golden_corpus()
SANITIZABLE = [note for note in CORPUS if note["expected_outcome"] == "sanitized"]
REJECTED = [note for note in CORPUS if note["expected_outcome"] == "high_risk_rejected"]

# Cases where de_core_news_lg genuinely cannot tag an annotated span. Populating this
# list is the ONLY sanctioned way to make a failure below go away other than fixing
# the detector — never edit the corpus to dodge a recall gap. Each entry is
# (note_id, entity_text) and must carry a comment saying why.
KNOWN_RECALL_GAPS: set[tuple[str, str]] = {
    # de_core_news_lg tags only the two "Hans Müller" mentions in note_012 (PER at
    # 0:11 and 104:115) and produces no entity at all for the salutation form
    # "Herr Müller" at 63:74. Layer 3 refines, it never rescans (design spec §2), so
    # with no layer-2 span there is nothing for the doctor/patient heuristic to
    # promote, and layer 1 owns only the six fixed-format identifier types — no
    # detector in the stack can reach this span. Measured, not hidden: this is a
    # real leak of a surname in the MVP, and the follow-up is a salutation-prefix
    # ("Herr"/"Frau") recognizer, not a corpus edit.
    ("note_012", "Herr Müller"),
}

# The mirror image of KNOWN_RECALL_GAPS: notes whose *own* sanitize() output the
# output guard rejects, because de_core_news_lg tags an ordinary German label word in
# the untouched prose as a PERSON. A precision gap, not a recall gap — nothing leaks,
# but the message fails closed and never reaches the user. Same rule applies: the
# corpus is never edited to hide one, and each entry records what the model produced.
KNOWN_GUARD_FALSE_POSITIVES: dict[str, str] = {
    # note_014 is "… Versichertennummer C112233445. Patientennummer: 9012347." On the
    # raw note de_core_news_lg emits PER over "C112233445. Patientennummer", which the
    # stack discards wholesale because it overlaps layer 1's INSURANCE_NUMBER span —
    # so "Patientennummer" stays in the clear, correctly, as a label word. When the
    # guard rescans the sanitized text the numbers are masked, the same model now
    # emits PER over the bare word "Patientennummer", and layer 3 promotes it to
    # PATIENT. (In note_005 the identical word is tagged PER at position 0 and *is*
    # tokenized, which is the same false positive landing on the other side of the
    # round trip.) Fixing this means either a label-word stoplist or truncating
    # partially-claimed layer-2 spans to their unclaimed remainder instead of
    # dropping them — both changes to reviewed layer 2/3 behavior, deliberately not
    # made here. See the task-12 report.
    "note_014": "Patientennummer",
}


@pytest.mark.parametrize("note", SANITIZABLE, ids=lambda note: note["id"])
def test_no_raw_pii_string_survives_sanitize(note, corpus_pipeline, corpus_scope):
    """Design spec §7 / master spec §9: no raw golden-corpus PII string ever appears
    in sanitize()'s output, for every corpus example."""
    tenant_id, conversation_id = corpus_scope
    sanitized = corpus_pipeline.sanitize(tenant_id, conversation_id, note["text"])

    leaked = [
        entity["text"]
        for entity in note["entities"]
        if entity["text"] in sanitized
        and (note["id"], entity["text"]) not in KNOWN_RECALL_GAPS
    ]
    assert not leaked, (
        f"{note['id']}: raw PII survived sanitize(): {leaked}\n"
        f"sanitized output: {sanitized}"
    )


@pytest.mark.parametrize("note", SANITIZABLE, ids=lambda note: note["id"])
def test_sanitize_emits_only_well_formed_tokens(note, corpus_pipeline, corpus_scope):
    tenant_id, conversation_id = corpus_scope
    sanitized = corpus_pipeline.sanitize(tenant_id, conversation_id, note["text"])
    for token in TOKEN_PATTERN.findall(sanitized):
        assert token.rsplit("_", 1)[0].isupper()


@pytest.mark.parametrize("note", SANITIZABLE, ids=lambda note: note["id"])
def test_round_trip_restores_the_original_note(note, corpus_pipeline, corpus_scope):
    tenant_id, conversation_id = corpus_scope
    sanitized = corpus_pipeline.sanitize(tenant_id, conversation_id, note["text"])

    false_positive = KNOWN_GUARD_FALSE_POSITIVES.get(note["id"])
    if false_positive is not None:
        # Asserted, not skipped: the note fails closed on a documented model
        # precision gap, and this pins that behavior so that fixing the gap (or
        # regressing further) shows up here rather than passing silently.
        assert false_positive in sanitized
        # Matched on the documented entity type, not just on the exception class: an
        # unrelated — and genuinely worse — leak appearing in this note must fail
        # here rather than pass under the comment above, which would then quietly
        # stop being true.
        with pytest.raises(LeakageDetectedError, match=r"\bPATIENT at\b"):
            corpus_pipeline.deanonymize(tenant_id, conversation_id, sanitized)
        return

    restored = corpus_pipeline.deanonymize(tenant_id, conversation_id, sanitized)
    assert restored == note["text"]


@pytest.mark.parametrize("note", REJECTED, ids=lambda note: note["id"])
def test_the_combination_case_is_rejected_not_partially_sanitized(
    note, corpus_pipeline, corpus_scope
):
    tenant_id, conversation_id = corpus_scope
    with pytest.raises(HighRiskMessageError) as excinfo:
        corpus_pipeline.sanitize(tenant_id, conversation_id, note["text"])
    for entity in note["entities"]:
        assert entity["text"] not in str(excinfo.value)


def test_the_coreference_limitation_leaves_the_salutation_form_untokenized(
    corpus_pipeline, corpus_scope
):
    """The MVP limitation, made visible — and measured as it actually behaves.

    note_012 refers to one human three times: "Hans Müller", "Herr Müller",
    "Hans Müller". Entity identity is exact-string, so the two identical surfaces
    share one token. The third reference does not merely get a *different* token, as
    the plan anticipated: de_core_news_lg produces no entity for "Herr Müller" at all
    (see KNOWN_RECALL_GAPS), so the surname is left in the clear. This test pins the
    real, worse outcome rather than the expected one, so that a future salutation
    recognizer — or any regression — changes this assertion.
    """
    tenant_id, conversation_id = corpus_scope
    note = next(n for n in CORPUS if n["id"] == "note_012")

    sanitized = corpus_pipeline.sanitize(tenant_id, conversation_id, note["text"])
    person_tokens = [
        token for token in TOKEN_PATTERN.findall(sanitized)
        if token.startswith(("PATIENT_", "PERSON_"))
    ]

    assert ("note_012", "Herr Müller") in KNOWN_RECALL_GAPS
    assert "Herr Müller" in sanitized, (
        "the salutation form is now detected — the recall gap recorded in "
        f"KNOWN_RECALL_GAPS is stale and must be removed: {sanitized}"
    )
    assert len(person_tokens) == 2 and len(set(person_tokens)) == 1, (
        "the two identical 'Hans Müller' surfaces no longer share exactly one "
        f"token; exact-string entity identity has changed: {sanitized}"
    )


def test_every_corpus_note_sanitizes_under_a_single_conversation(
    corpus_pipeline, corpus_scope
):
    """One conversation, every note: token minting must not collide or fail on the
    (tenant_id, conversation_id, token) unique constraint across many messages."""
    tenant_id, conversation_id = corpus_scope
    for note in SANITIZABLE:
        sanitized = corpus_pipeline.sanitize(tenant_id, conversation_id, note["text"])
        assert sanitized


def test_a_corpus_token_never_resolves_for_another_tenant(
    corpus_pipeline, corpus_scope, corpus_key_provider
):
    tenant_id, conversation_id = corpus_scope
    other_tenant_id, _ = new_scope(corpus_key_provider)
    note = next(n for n in SANITIZABLE if n["id"] == "note_001")

    sanitized = corpus_pipeline.sanitize(tenant_id, conversation_id, note["text"])

    with pytest.raises(UnresolvedTokenError):
        corpus_pipeline.deanonymize(other_tenant_id, conversation_id, sanitized)


def test_a_corpus_token_never_resolves_for_another_conversation(
    corpus_pipeline, corpus_scope, corpus_key_provider
):
    tenant_id, conversation_id = corpus_scope
    _, other_conversation_id = new_scope(corpus_key_provider)
    note = next(n for n in SANITIZABLE if n["id"] == "note_001")

    sanitized = corpus_pipeline.sanitize(tenant_id, conversation_id, note["text"])

    with pytest.raises(UnresolvedTokenError):
        corpus_pipeline.deanonymize(tenant_id, other_conversation_id, sanitized)


@pytest.mark.parametrize(
    "leaked_output",
    [
        "Die Versichertennummer lautet A123456789.",
        "Lukas Berger wurde am 12.03.2024 entlassen.",
        "Bitte melden Sie sich unter s.vogel@example.de.",
        "Rückruf unter 0941 5551234 vereinbart.",
    ],
)
def test_raw_pii_in_llm_output_is_always_caught(
    leaked_output, corpus_pipeline, corpus_scope
):
    tenant_id, conversation_id = corpus_scope
    with pytest.raises(LeakageDetectedError):
        corpus_pipeline.deanonymize(tenant_id, conversation_id, leaked_output)


def test_a_token_mixed_with_leaked_pii_still_fails_closed(corpus_pipeline, corpus_scope):
    tenant_id, conversation_id = corpus_scope
    note = next(n for n in SANITIZABLE if n["id"] == "note_001")
    sanitized = corpus_pipeline.sanitize(tenant_id, conversation_id, note["text"])

    with pytest.raises(LeakageDetectedError):
        corpus_pipeline.deanonymize(
            tenant_id, conversation_id, sanitized + " Kontakt: A987654321."
        )
