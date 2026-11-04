"""Convert ingredient amounts between metric and US customary units.

Spoons stay spoons in both systems: they are the same size everywhere a recipe
is likely to be read, and "15 ml salt" helps nobody. Counts and unknown units
are never converted.
"""

from __future__ import annotations

from units import canonicalize_unit, unit_family

SYSTEMS = ("original", "metric", "us")

_US_SMALL_MASS_G = 14.0  # under half an ounce, grams read better than "0.2 oz"


def _metric(amount: float, family: str, size: float) -> tuple[float, str]:
    base = amount * size
    if family == "mass":
        return (base / 1000, "kg") if base >= 1000 else (base, "g")
    return (base / 1000, "l") if base >= 1000 else (base, "ml")


def _us(amount: float, family: str, size: float) -> tuple[float, str]:
    base = amount * size
    if family == "mass":
        if base < _US_SMALL_MASS_G:
            return base, "g"
        ounces = base / 28.35
        return (ounces / 16, "lb") if ounces >= 16 else (ounces, "oz")
    if base < 15:
        return base / 5, "tsp"
    if base < 60:
        return base / 15, "tbsp"
    return base / 240, "cup"


def convert_amount(amount: float, unit: str, system: str) -> tuple[float, str]:
    """``(amount, unit)`` in ``system``. Leaves anything it cannot convert alone."""
    if system not in ("metric", "us") or amount is None:
        return amount, unit
    canonical = canonicalize_unit(unit)
    family, size = unit_family(canonical)
    if family not in ("mass", "volume"):
        return amount, unit
    if canonical in ("tsp", "tbsp"):
        return amount, canonical
    if system == "metric":
        return _metric(amount, family, size)
    if canonical in ("oz", "lb", "cup", "fl oz"):
        return amount, canonical
    return _us(amount, family, size)


def to_base(amount: float, unit: str) -> tuple[float, str]:
    """``(amount, family)`` in grams, millilitres or a bare count.

    Used to add up the same food across recipes: "200 g" and "0.5 kg" sum to
    700 g, "1 cup" and "4 tbsp" sum to 300 ml. Unknown units come back as
    family ``"unit:<name>"`` so they only merge with themselves.
    """
    canonical = canonicalize_unit(unit)
    family, size = unit_family(canonical)
    if family in ("mass", "volume"):
        return amount * size, family
    if family == "count" or not canonical:
        return amount, "count"
    return amount, f"unit:{canonical.casefold()}"


def from_base(amount: float, family: str) -> tuple[float, str]:
    """Pick a readable unit for a summed base amount."""
    if family == "mass":
        return (amount / 1000, "kg") if amount >= 1000 else (amount, "g")
    if family == "volume":
        return (amount / 1000, "l") if amount >= 1000 else (amount, "ml")
    if family == "count":
        return amount, ""
    return amount, family.split(":", 1)[1] if ":" in family else ""
