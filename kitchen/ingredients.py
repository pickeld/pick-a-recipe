"""Split a free-text ingredient line into quantity, unit, food and notes.

Extraction already returns structured ingredients. This is for everything
else: Mealie/Tandoor/Paprika imports, Schema.org pages with only
``recipeIngredient`` strings, and lines a person types into the editor.
"""

from __future__ import annotations

import re

from kitchen.quantities import parse_quantity
from units import canonicalize_unit, unit_family

_QTY_TOKEN = re.compile(
    r"^(?:\d+(?:[.,]\d+)?(?:/\d+)?|[½⅓⅔¼¾⅕⅖⅗⅘⅙⅚⅛⅜⅝⅞]|\d+[½⅓⅔¼¾⅛⅜⅝⅞]"
    r"|[-–]|to|עד|או|ו?חצי|ו?רבע|ו?שליש|a|an|one|two|three|four|five|six|"
    r"half|dozen|אחד|אחת|שתיים|שניים|שתי|שני|שלוש|שלושה|ארבע|ארבעה|חמש|חמישה|כ-?\d+(?:[.,]\d+)?)$",
    re.IGNORECASE,
)

_OPTIONAL = re.compile(r"\b(optional)\b|\(אופציונלי\)|אופציונלי", re.IGNORECASE)


def _is_unit(token: str) -> bool:
    family, _ = unit_family(token)
    return bool(family) or canonicalize_unit(token) != token.strip()


def parse_ingredient_line(line: str) -> dict:
    """Best-effort split. Anything not understood stays in ``food``."""
    raw = " ".join(str(line or "").split())
    text = raw
    notes_parts: list[str] = []

    for match in re.findall(r"\(([^)]*)\)", text):
        if match.strip():
            notes_parts.append(match.strip())
    text = re.sub(r"\([^)]*\)", " ", text)

    if "," in text:
        head, _, tail = text.partition(",")
        if tail.strip():
            notes_parts.append(tail.strip())
        text = head

    tokens = text.split()
    qty_tokens: list[str] = []
    while tokens and _QTY_TOKEN.match(tokens[0].strip(".")):
        qty_tokens.append(tokens.pop(0))
    # "a" alone is often the article in "a pinch of salt"; keep it as 1.
    unit = ""
    if tokens:
        two = " ".join(tokens[:2])
        if len(tokens) >= 2 and _is_unit(two):
            unit, tokens = two, tokens[2:]
        elif _is_unit(tokens[0].rstrip(".")):
            unit, tokens = tokens[0].rstrip("."), tokens[1:]
    if tokens and tokens[0].casefold() in ("of", "של"):
        tokens = tokens[1:]

    food = " ".join(tokens).strip(" -–,")
    quantity_text = " ".join(qty_tokens)
    if not food:
        food, quantity_text, unit = raw, "", ""

    notes = ", ".join(notes_parts)
    optional = bool(_OPTIONAL.search(raw))
    parsed = parse_quantity(quantity_text)
    return {
        "food": food,
        "quantity": quantity_text if parsed.known else "",
        "unit": canonicalize_unit(unit) if unit else "",
        "notes": notes,
        "raw": raw,
        "optional": optional,
    }
