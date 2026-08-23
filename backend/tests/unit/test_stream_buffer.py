from app.llm_gateway.stream_buffer import SentenceBuffer


def test_single_sentence_completes_once_punctuation_and_a_space_arrive():
    buffer = SentenceBuffer()
    assert buffer.feed("Hallo, wie geht es") == []
    assert buffer.feed(".") == []  # no trailing whitespace yet -- not complete
    assert buffer.feed(" Ihnen heute?") == ["Hallo, wie geht es. "]


def test_multiple_sentences_in_one_delta_yield_multiple_chunks():
    buffer = SentenceBuffer()
    completed = buffer.feed("Erster Satz. Zweiter Satz! Dritter Satz? Rest ohne Ende")
    assert completed == ["Erster Satz. ", "Zweiter Satz! ", "Dritter Satz? "]


def test_paragraph_break_is_also_a_boundary():
    buffer = SentenceBuffer()
    completed = buffer.feed("Absatz eins ohne Punkt\n\nAbsatz zwei")
    assert completed == ["Absatz eins ohne Punkt\n\n"]


def test_char_by_char_feed_accumulates_and_only_flushes_at_a_boundary():
    buffer = SentenceBuffer()
    completed: list[str] = []
    for char in "Ok. ":
        completed.extend(buffer.feed(char))
    assert completed == ["Ok. "]


def test_flush_remainder_returns_the_trailing_partial_chunk():
    buffer = SentenceBuffer()
    buffer.feed("Ein vollständiger Satz. Und ein Rest ohne Satzzeichen")
    assert buffer.flush_remainder() == "Und ein Rest ohne Satzzeichen"


def test_flush_remainder_is_none_when_nothing_is_buffered():
    buffer = SentenceBuffer()
    buffer.feed("Nur ein Satz. ")
    assert buffer.flush_remainder() is None


def test_a_token_immediately_before_a_boundary_is_never_split():
    buffer = SentenceBuffer()
    completed = buffer.feed("Patient PATIENT_A1B2C3D4E5 wurde entlassen. Weiterer Satz")
    assert completed == ["Patient PATIENT_A1B2C3D4E5 wurde entlassen. "]
