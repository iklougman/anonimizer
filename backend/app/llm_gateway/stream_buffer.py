from __future__ import annotations

import re

# Splits after a sentence-ending punctuation mark followed by whitespace, or
# at a paragraph break -- the granularity chat.py's _stream_and_guard() runs
# the (unmodified) output guard over, one chunk at a time, instead of waiting
# for the full response (see app/privacy_gateway/output_guard/guard.py).
#
# A token (guard.py's TOKEN_PATTERN: `[A-Z][A-Z_]*_` + exactly 10 uppercase hex
# digits) can never straddle a chunk boundary produced by this pattern: token
# characters are letters/digits/underscore only, so a completed match's
# consumed text (the whitespace run, or the literal "\n\n") can never end
# inside one -- the boundary-defining characters themselves are never valid
# token characters. No separate token-straddle check is needed.
_SENTENCE_BOUNDARY_PATTERN = re.compile(r"(?<=[.!?])\s+|\n\n")


class SentenceBuffer:
    """Accumulates streamed text deltas and releases complete sentence/
    paragraph chunks as they close."""

    def __init__(self) -> None:
        self._buffer = ""

    def feed(self, delta: str) -> list[str]:
        """Append `delta`; return the newly-completed chunks (each ending at a
        sentence/paragraph boundary), in order. May return an empty list if no
        boundary has completed yet."""
        self._buffer += delta
        completed: list[str] = []
        while True:
            match = _SENTENCE_BOUNDARY_PATTERN.search(self._buffer)
            if match is None:
                break
            completed.append(self._buffer[: match.end()])
            self._buffer = self._buffer[match.end() :]
        return completed

    def flush_remainder(self) -> str | None:
        """Call once after the provider's stream ends. Returns the trailing
        partial chunk (which may not end in punctuation), or None if nothing
        is buffered."""
        remainder, self._buffer = self._buffer, ""
        return remainder or None
