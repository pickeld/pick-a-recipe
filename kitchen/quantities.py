"""Parse, scale and print ingredient quantities.

Quantities arrive as free text from extraction or from a person typing: "1/2",
"1½", "2-3", "1,5", "חצי", "1 וחצי". Scaling works on the parsed number; the
original text is kept for display whenever nothing was scaled.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_UNICODE_FRACTIONS = {
    "½": 1 / 2, "⅓": 1 / 3, "⅔": 2 / 3, "¼": 1 / 4, "¾": 3 / 4,
    "⅕": 1 / 5, "⅖": 2 / 5, "⅗": 3 / 5, "⅘": 4 / 5, "⅙": 1 / 6, "⅚": 5 / 6,
    "⅛": 1 / 8, "⅜": 3 / 8, "⅝": 5 / 8, "⅞": 7 / 8,
}

_ARTICLES = {"a", "an"}
_MULTIPLIERS = {"dozen": 12, "תריסר": 12}

_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "dozen": 12, "half": 1 / 2, "quarter": 1 / 4, "third": 1 / 3,
    "אחד": 1, "אחת": 1, "שניים": 2, "שנים": 2, "שתיים": 2, "שתים": 2, "שני": 2,
    "שתי": 2, "שלוש": 3, "שלושה": 3, "ארבע": 4, "ארבעה": 4, "חמש": 5,
    "חמישה": 5, "שש": 6, "שישה": 6, "שבע": 7, "שבעה": 7, "שמונה": 8,
    "תשע": 9, "תשעה": 9, "עשר": 10, "עשרה": 10, "חצי": 1 / 2, "רבע": 1 / 4,
    "שליש": 1 / 3,
}

# Words that can sit in a quantity without changing it.
_FILLER = {"and", "about", "approx", "approximately", "around", "ו", "כ", "בערך", "כמעט"}

_RANGE_SPLIT = re.compile(r"\s*(?:-|–|—|\bto\b|\bor\b|\bעד\b|\bאו\b)\s*")
_NUMBER = re.compile(r"^\d+(?:[.,]\d+)?$")
_FRACTION = re.compile(r"^(\d+)\s*/\s*(\d+)$")

_NOT_SCALABLE_TEXT = (
    "to taste", "as needed", "for serving", "for garnish", "optional garnish",
    "לפי הטעם", "לפי הצורך", "להגשה", "לקישוט",
)


@dataclass(frozen=True)
class Quantity:
    value: float | None
    max: float | None = None

    @property
    def known(self) -> bool:
        return self.value is not None


def _normalize(text: str) -> str:
    out = []
    for ch in text:
        if ch in _UNICODE_FRACTIONS:
            out.append(f" {_UNICODE_FRACTIONS[ch]!r} ")
        elif ch == "⁄":
            out.append("/")
        else:
            out.append(ch)
    text = "".join(out)
    text = re.sub(r"(?<=\d)\s*/\s*(?=\d)", "/", text)
    text = re.sub(r"[~≈]", " ", text)
    # Hebrew "and" and "approximately" prefixes: "וחצי", "כ-200", "כ200".
    text = re.sub(r"(?:(?<=\s)|^)ו(?=\S)", "ו ", text)
    text = re.sub(r"(?:(?<=\s)|^)כ-?(?=\d)", "כ ", text)
    return " ".join(text.split())


def _parse_single(text: str) -> float | None:
    total = 0.0
    seen = False
    article = False
    for token in text.split():
        token = token.strip("()[],;").casefold()
        if not token or token in _FILLER:
            continue
        if token in _ARTICLES:
            article = True
            continue
        if token in _MULTIPLIERS:
            total = (total if seen else 1.0) * _MULTIPLIERS[token]
            seen = True
            continue
        frac = _FRACTION.match(token)
        if frac:
            den = int(frac.group(2))
            if den == 0:
                return None
            total += int(frac.group(1)) / den
            seen = True
        elif _NUMBER.match(token):
            total += float(token.replace(",", "."))
            seen = True
        elif token in _WORDS:
            total += _WORDS[token]
            seen = True
        else:
            try:
                total += float(token)
                seen = True
            except ValueError:
                continue
    if not seen and article:
        return 1.0
    return total if seen else None


def parse_quantity(text: str | float | int | None) -> Quantity:
    """Parse a free-text quantity. Unknown text gives ``Quantity(None)``."""
    if text is None:
        return Quantity(None)
    if isinstance(text, (int, float)):
        return Quantity(float(text)) if text > 0 else Quantity(None)
    normalized = _normalize(str(text))
    if not normalized:
        return Quantity(None)
    # A dash between two numbers is a range; a leading dash is not.
    parts = [p for p in _RANGE_SPLIT.split(normalized) if p.strip()]
    if len(parts) >= 2:
        low, high = _parse_single(parts[0]), _parse_single(parts[1])
        if low is not None and high is not None and high > low:
            return Quantity(low, high)
        if low is not None:
            return Quantity(low)
    value = _parse_single(normalized)
    if value is None or value <= 0:
        return Quantity(None)
    return Quantity(value)


def is_scalable(quantity: Quantity, unit: str, notes: str = "", food: str = "") -> bool:
    from units import unit_family

    if not quantity.known:
        return False
    if unit_family(unit)[0] == "pinch":
        return False
    haystack = f"{notes} {food}".casefold()
    return not any(marker in haystack for marker in _NOT_SCALABLE_TEXT)


def scale(quantity: Quantity, factor: float) -> Quantity:
    if not quantity.known or factor <= 0:
        return quantity
    high = quantity.max * factor if quantity.max is not None else None
    return Quantity(quantity.value * factor, high)  # type: ignore[operator]


_FRACTION_GLYPHS = {
    (1, 8): "⅛", (1, 4): "¼", (1, 3): "⅓", (3, 8): "⅜", (1, 2): "½",
    (5, 8): "⅝", (2, 3): "⅔", (3, 4): "¾", (7, 8): "⅞",
}
_FRACTION_CHOICES = [(n, d, n / d) for (n, d) in _FRACTION_GLYPHS]

# Units cooks measure with spoons, cups or by count read better as fractions.
FRACTION_UNITS = frozenset({"tsp", "tbsp", "cup", "fl oz", "oz", "lb", "piece", "pinch", ""})


def _trim(number: float, places: int) -> str:
    text = f"{number:.{places}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def format_fraction(value: float) -> str:
    whole = int(value)
    rest = value - whole
    if rest < 1 / 16:
        return str(whole) if whole else _trim(value, 2)
    if rest > 15 / 16:
        return str(whole + 1)
    n, d, _ = min(_FRACTION_CHOICES, key=lambda c: abs(c[2] - rest))
    glyph = _FRACTION_GLYPHS[(n, d)]
    return f"{whole}{glyph}" if whole else glyph


def format_decimal(value: float) -> str:
    if value >= 1000:
        return str(int(round(value / 10) * 10))
    if value >= 100:
        return str(int(round(value / 5) * 5))
    if value >= 10:
        return str(int(round(value)))
    if value >= 1:
        return _trim(value, 1)
    return _trim(value, 2)


def format_amount(value: float, unit: str) -> str:
    from units import canonicalize_unit

    canonical = canonicalize_unit(unit) if unit else ""
    if canonical in ("kg", "l"):
        return _trim(value, 2)
    if canonical in FRACTION_UNITS or canonical not in ("g", "ml"):
        return format_fraction(value)
    return format_decimal(value)


def format_quantity(quantity: Quantity, unit: str) -> str:
    if not quantity.known:
        return ""
    text = format_amount(quantity.value, unit)  # type: ignore[arg-type]
    if quantity.max is not None:
        text += "–" + format_amount(quantity.max, unit)
    return text
