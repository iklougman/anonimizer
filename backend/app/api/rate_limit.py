from __future__ import annotations

import threading
import time
from collections import defaultdict

_WINDOW_SECONDS = 3600
_lock = threading.Lock()
_hits: dict[str, list[float]] = defaultdict(list)


class RateLimitExceededError(Exception):
    """The caller has exceeded the allowed number of requests for this key
    within the current window."""


def check_rate_limit(key: str, limit_per_hour: int) -> None:
    """In-memory, per-process sliding-window limiter. Explicit known
    limitation (see ADR-0025): this is per-uvicorn-worker, not shared
    across workers or process restarts -- under UVICORN_WORKERS=4 the
    effective ceiling is up to 4x limit_per_hour. Accepted MVP scope for
    the one public endpoint (signup) this guards; a Postgres/Redis-backed
    limiter is a named follow-up if abuse is observed in practice."""
    now = time.monotonic()
    with _lock:
        recent = [t for t in _hits[key] if now - t < _WINDOW_SECONDS]
        if len(recent) >= limit_per_hour:
            _hits[key] = recent
            raise RateLimitExceededError(f"rate limit exceeded for {key!r}")
        recent.append(now)
        _hits[key] = recent


def _reset_for_tests() -> None:
    with _lock:
        _hits.clear()
