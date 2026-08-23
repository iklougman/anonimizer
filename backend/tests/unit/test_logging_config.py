import logging

from app.logging_config import ColorFormatter


def _record(name: str, level: int, message: str) -> logging.LogRecord:
    return logging.LogRecord(name, level, "test.py", 1, message, args=None, exc_info=None)


def test_app_info_lines_are_colored():
    formatted = ColorFormatter("%(message)s").format(
        _record("app.api.chat", logging.INFO, "chat.receive")
    )
    assert formatted.startswith("\033[32m")
    assert formatted.endswith("\033[0m")
    assert "chat.receive" in formatted


def test_warning_and_info_get_different_colors():
    info = ColorFormatter("%(message)s").format(_record("app.api.chat", logging.INFO, "x"))
    warning = ColorFormatter("%(message)s").format(_record("app.api.chat", logging.WARNING, "x"))
    assert info != warning


def test_noisy_third_party_loggers_are_dimmed_not_colored_by_level():
    formatted = ColorFormatter("%(message)s").format(
        _record("presidio-analyzer", logging.WARNING, "Entity MISC is not mapped")
    )
    assert formatted.startswith("\033[2m")
    assert "\033[33m" not in formatted  # not the WARNING color


def test_httpx_lines_are_dimmed():
    formatted = ColorFormatter("%(message)s").format(
        _record("httpx", logging.INFO, "HTTP Request: POST ...")
    )
    assert formatted.startswith("\033[2m")
