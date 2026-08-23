from __future__ import annotations

import logging

# Presidio emits one WARNING per unmapped NER label per detector call -- dozens per
# request -- and httpx logs one INFO line per outbound HTTP call. Both are frequent
# enough to bury the app's own step-by-step decision log (app.api.*,
# app.privacy_gateway.*) in a wall of identical-looking text, so they're dimmed
# rather than colored by level like everything else.
_DIMMED_LOGGER_PREFIXES = ("presidio", "httpx", "httpcore")

_RESET = "\033[0m"
_DIM = "\033[2m"
_LEVEL_COLORS = {
    logging.DEBUG: "\033[36m",  # cyan
    logging.INFO: "\033[32m",  # green
    logging.WARNING: "\033[33m",  # yellow
    logging.ERROR: "\033[31m",  # red
    logging.CRITICAL: "\033[1;31m",  # bold red
}


class ColorFormatter(logging.Formatter):
    """Colors each line by level, dims known-noisy third-party loggers.

    ANSI codes are emitted unconditionally (no isatty() check): the target for
    this formatter is `docker logs` on the host, where the container's own stdout
    is never a real tty even though the viewer's terminal renders the codes fine.
    """

    def format(self, record: logging.LogRecord) -> str:
        message = super().format(record)
        if record.name.startswith(_DIMMED_LOGGER_PREFIXES):
            return f"{_DIM}{message}{_RESET}"
        color = _LEVEL_COLORS.get(record.levelno, "")
        return f"{color}{message}{_RESET}" if color else message


def configure_logging(level: str, colorize: bool) -> None:
    handler = logging.StreamHandler()
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    handler.setFormatter(ColorFormatter(fmt) if colorize else logging.Formatter(fmt))
    logging.basicConfig(level=level, handlers=[handler], force=True)
