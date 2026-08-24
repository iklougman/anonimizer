import decimal

from app.llm_gateway.provider import ChatMessage, StreamDelta, StreamUsage
from app.llm_gateway.stub_provider import StubProvider


def test_name_and_model():
    provider = StubProvider()
    assert provider.name == "stub"
    assert provider.model == "stub-echo-v1"


def test_echoes_the_last_user_message_verbatim():
    provider = StubProvider()
    messages = [
        ChatMessage(role="system", content="be helpful"),
        ChatMessage(role="user", content="Patient PATIENT_A1B2C3D4E5 aufgenommen."),
    ]
    items = list(provider.stream(messages))

    deltas = [item for item in items if isinstance(item, StreamDelta)]
    reassembled = "".join(delta.text for delta in deltas)
    assert reassembled == "Patient PATIENT_A1B2C3D4E5 aufgenommen."


def test_stream_ends_with_exactly_one_usage_item():
    provider = StubProvider()
    messages = [ChatMessage(role="user", content="Hallo Welt")]
    items = list(provider.stream(messages))

    assert isinstance(items[-1], StreamUsage)
    assert sum(1 for item in items if isinstance(item, StreamUsage)) == 1
    usage = items[-1]
    assert usage.tokens_in > 0
    assert usage.tokens_out > 0
    assert usage.cost_usd == decimal.Decimal(0)


def test_yields_multiple_deltas_not_one_giant_chunk():
    """Mirrors real streaming providers' multi-delta shape (word-chunked),
    exercising the same SentenceBuffer assembly path in app/api/chat.py a
    real provider does -- a single-delta stub would silently skip testing
    that assembly logic."""
    provider = StubProvider()
    messages = [ChatMessage(role="user", content="Eins zwei drei vier fünf")]
    deltas = [item for item in provider.stream(messages) if isinstance(item, StreamDelta)]
    assert len(deltas) > 1


def test_ignores_system_and_assistant_messages_uses_last_user_only():
    provider = StubProvider()
    messages = [
        ChatMessage(role="system", content="system prompt text"),
        ChatMessage(role="user", content="first user turn"),
        ChatMessage(role="assistant", content="assistant reply"),
        ChatMessage(role="user", content="second user turn"),
    ]
    items = list(provider.stream(messages))
    reassembled = "".join(item.text for item in items if isinstance(item, StreamDelta))
    assert reassembled == "second user turn"
