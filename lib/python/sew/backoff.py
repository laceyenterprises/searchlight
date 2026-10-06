"""Vendored for standalone SEW from agent_os_config/backoff (SWX-02)."""

from __future__ import annotations

import random
from typing import Callable

__all__ = ["FULL_JITTER", "NO_JITTER", "bounded_exponential_delay"]

#: AWS-style full jitter: sample uniformly from ``[0, ceiling]``. Spreads a
#: thundering herd; the right default for anything retrying against a shared
#: service.
FULL_JITTER = "full"

#: No jitter: return the ceiling exactly. Correct when the caller is the only
#: client, or when a test needs the delay to be deterministic.
NO_JITTER = "none"


def bounded_exponential_delay(
    attempt: int,
    *,
    base_seconds: float,
    max_seconds: float,
    jitter: str = NO_JITTER,
    rng: Callable[[float, float], float] | None = None,
) -> float:
    """Return the delay before retry number ``attempt``, in seconds.

    ``attempt`` is zero-based: attempt 0 is the first retry and waits
    ``base_seconds``. Callers that count from one pass ``attempt - 1``.

    The curve is ``base_seconds * 2 ** attempt``, clamped to ``max_seconds``
    and floored at zero. With ``jitter=FULL_JITTER`` the result is sampled
    uniformly from ``[0, ceiling]``.

    A negative ``attempt`` is clamped to 0 rather than returning a fraction of
    the base delay — every private copy this replaces did the same, via
    ``max(0, attempt)`` or ``max(attempt - 1, 0)``.
    """
    if base_seconds <= 0 or max_seconds <= 0:
        return 0.0
    attempt = max(int(attempt), 0)
    if attempt >= 1024:
        ceiling = float(max_seconds)
    else:
        exponential = base_seconds * (2**attempt)
        ceiling = min(float(max_seconds), exponential)
    if ceiling <= 0:
        return 0.0
    if jitter == FULL_JITTER:
        sample = rng or random.uniform
        return max(0.0, sample(0.0, ceiling))
    if jitter != NO_JITTER:
        raise ValueError(f"bounded_exponential_delay: unknown jitter mode {jitter!r}")
    return ceiling
