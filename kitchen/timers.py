"""Find cooking durations in step text so cook mode can offer a timer."""

from __future__ import annotations

import re

_UNIT_SECONDS = {
    "second": 1, "seconds": 1, "sec": 1, "secs": 1, "s": 1,
    "minute": 60, "minutes": 60, "min": 60, "mins": 60, "m": 60,
    "hour": 3600, "hours": 3600, "hr": 3600, "hrs": 3600, "h": 3600,
    "שנייה": 1, "שניות": 1, "דקה": 60, "דקות": 60, "דק'": 60, "דק": 60,
    "שעה": 3600, "שעות": 3600,
}

_NUM = r"\d+(?:[.,]\d+)?"
_UNITS = "|".join(sorted((re.escape(u) for u in _UNIT_SECONDS if len(u) > 1), key=len, reverse=True))
_DURATION = re.compile(
    rf"(?P<low>{_NUM})(?:\s*(?:-|–|to|עד)\s*(?P<high>{_NUM}))?\s*-?\s*(?P<unit>{_UNITS})(?![A-Za-z\u0590-\u05FF])",
    re.IGNORECASE,
)
_HEBREW_WORDS = re.compile(r"(?P<word>חצי שעה|רבע שעה|שעה וחצי|שעתיים|דקה)(?![\u0590-\u05FF])")
_HEBREW_WORD_SECONDS = {"חצי שעה": 1800, "רבע שעה": 900, "שעה וחצי": 5400, "שעתיים": 7200, "דקה": 60}


def _num(text: str) -> float:
    return float(text.replace(",", "."))


def detect_timers(text: str) -> list[dict]:
    """``[{"seconds", "max_seconds", "label"}]`` for each duration in ``text``.

    For a range ("10-12 minutes") the timer starts at the low end; the high end
    is kept so the UI can say "check at 10, done by 12".
    """
    found: list[dict] = []
    taken: list[tuple[int, int]] = []
    for match in _DURATION.finditer(text or ""):
        size = _UNIT_SECONDS[match.group("unit").casefold()]
        low = int(_num(match.group("low")) * size)
        high = int(_num(match.group("high")) * size) if match.group("high") else None
        if low <= 0 or low > 48 * 3600:
            continue
        found.append({"seconds": low, "max_seconds": high, "label": match.group(0).strip()})
        taken.append(match.span())
    for match in _HEBREW_WORDS.finditer(text or ""):
        if any(s <= match.start() < e for s, e in taken):
            continue
        found.append({
            "seconds": _HEBREW_WORD_SECONDS[match.group("word")],
            "max_seconds": None,
            "label": match.group("word"),
        })
    return found
