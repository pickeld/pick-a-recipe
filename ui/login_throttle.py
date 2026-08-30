"""Progressive backoff for failed sign-in attempts.

Held in memory: this app runs as a single process, and a restart clearing the
counters is an acceptable trade for having no external dependency. Attackers
cannot trigger restarts, so the window this opens is not theirs to exploit.

Deliberately not a lockout. An attacker who knows a username could otherwise
lock its owner out at will, turning a brute-force defence into a denial of
service. Attempts are slowed instead, and the delay decays on its own.
"""

import threading
import time

# Free attempts before any delay, then the delay doubles per failure. Reaching
# the cap takes a handful of wrong guesses; sustained guessing settles at one
# attempt per _MAX_DELAY, which makes an online attack useless without ever
# denying the real owner access for long.
_FREE_ATTEMPTS = 3
_BASE_DELAY = 2.0
_MAX_DELAY = 60.0

# Failures older than this are forgotten, so a legitimate user who mistyped
# their password an hour ago starts clean.
_WINDOW = 900.0

_lock = threading.Lock()
_failures: dict[str, tuple[int, float]] = {}


def _prune(now: float) -> None:
    """Drop stale entries. Called under _lock."""
    for key, (_, last_seen) in list(_failures.items()):
        if now - last_seen > _WINDOW:
            del _failures[key]


def retry_after(key: str) -> float:
    """Seconds the caller must wait before another attempt, or 0.0 if clear."""
    now = time.monotonic()
    with _lock:
        _prune(now)
        entry = _failures.get(key)
        if entry is None:
            return 0.0
        count, last_seen = entry
        if count <= _FREE_ATTEMPTS:
            return 0.0
        delay = min(_BASE_DELAY * (2 ** (count - _FREE_ATTEMPTS - 1)), _MAX_DELAY)
        remaining = delay - (now - last_seen)
        return max(0.0, remaining)


def record_failure(key: str) -> None:
    """Count a failed attempt against a key."""
    now = time.monotonic()
    with _lock:
        _prune(now)
        count, _ = _failures.get(key, (0, now))
        _failures[key] = (count + 1, now)


def reset(key: str) -> None:
    """Forget a key's failures, after a successful sign-in."""
    with _lock:
        _failures.pop(key, None)


def clear_all() -> None:
    """Wipe all counters. For tests."""
    with _lock:
        _failures.clear()
