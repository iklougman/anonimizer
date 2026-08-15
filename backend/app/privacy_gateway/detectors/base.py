from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, order=True)
class Span:
    """One detected sensitive region of a message.

    Field order matters: `order=True` sorts spans by `(start, end, entity_type, ...)`,
    which is the ascending-offset order every layer returns and the pseudonymizer
    reverses.
    """

    start: int
    end: int
    entity_type: str
    confidence: float
    source_layer: str


def spans_overlap(a: Span, b: Span) -> bool:
    """True if two half-open [start, end) ranges share at least one character."""
    return a.start < b.end and b.start < a.end


def is_claimed(candidate: Span, claimed: Sequence[Span]) -> bool:
    """Design spec §2: each layer only processes text not already claimed by a
    higher-precedence layer. Precedence is fixed (regex > Presidio > custom), never
    resolved by comparing confidence scores.
    """
    return any(spans_overlap(candidate, existing) for existing in claimed)


# Unicode-aware: Python 3's `\w` matches every Unicode letter and digit, not just
# ASCII. A hardcoded ASCII + German-umlaut class silently dropped any other accented
# letter, so "Charité" normalized to "charit" and "Hôpital" to "h pital" — a recall
# gap for every loanword-origin facility or disease name. `\w` also matches the
# underscore, which does not occur in clinical prose or facility names.
_WORD_PATTERN = re.compile(r"[\w-]+")


def normalize(value: str) -> str:
    """Case-folded, punctuation-stripped, single-spaced form used for every
    gazetteer and rare-disease-name comparison, so both sides of a lookup are
    normalized identically.
    """
    return " ".join(_WORD_PATTERN.findall(value)).casefold()


@runtime_checkable
class Detector(Protocol):
    """Layers 1 and 2: scan raw text, skipping anything already claimed."""

    layer_name: str

    def detect(self, text: str, claimed: Sequence[Span]) -> list[Span]: ...


@runtime_checkable
class SpanRefiner(Protocol):
    """Layer 3: refine, don't rescan (design spec §2) — takes the spans produced by
    layers 1 and 2 and returns a re-tagged set, never new offsets."""

    layer_name: str

    def refine(self, text: str, spans: Sequence[Span]) -> list[Span]: ...
