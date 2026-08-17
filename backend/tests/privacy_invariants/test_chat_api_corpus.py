import decimal

import pytest
from fastapi.testclient import TestClient

from app.auth.dependencies import get_current_user
from app.auth.tenant_resolver import AuthenticatedUser
from app.llm_gateway.provider import LLMCompletion
from app.llm_gateway.registry import get_provider
from app.main import app
from app.privacy_gateway.pipeline import get_pipeline
from tests.privacy_invariants.conftest import new_scope_with_user
from tests.privacy_invariants.test_pipeline_corpus import KNOWN_RECALL_GAPS, SANITIZABLE


class _RecordingProvider:
    name = "recording-stub"
    model = "stub-model"

    def __init__(self) -> None:
        self.captured_prompts: list[str] = []

    def complete(self, prompt: str) -> LLMCompletion:
        self.captured_prompts.append(prompt)
        return LLMCompletion(
            text="Verstanden.", tokens_in=1, tokens_out=1, cost_usd=decimal.Decimal("0")
        )


@pytest.mark.parametrize("note", SANITIZABLE, ids=lambda note: note["id"])
def test_outbound_llm_prompt_never_contains_raw_corpus_pii(
    note, corpus_pipeline, corpus_key_provider
):
    tenant_id, user_id, conversation_id = new_scope_with_user(corpus_key_provider)
    recording_provider = _RecordingProvider()

    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        tenant_id=tenant_id, user_id=user_id, role="doctor"
    )
    app.dependency_overrides[get_pipeline] = lambda: corpus_pipeline
    app.dependency_overrides[get_provider] = lambda: recording_provider
    client = TestClient(app)

    try:
        response = client.post(
            f"/api/conversations/{conversation_id}/messages", json={"content": note["text"]}
        )
        assert response.status_code == 200

        leaked = [
            entity["text"]
            for entity in note["entities"]
            for prompt in recording_provider.captured_prompts
            if entity["text"] in prompt and (note["id"], entity["text"]) not in KNOWN_RECALL_GAPS
        ]
        assert not leaked, (
            f"{note['id']}: raw PII reached the LLM provider: {leaked}\n"
            f"captured prompts: {recording_provider.captured_prompts}"
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_pipeline, None)
        app.dependency_overrides.pop(get_provider, None)
