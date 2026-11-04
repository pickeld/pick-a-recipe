"""The shared pantry: what is in the house, staples, and what expires soon."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from kitchen.aisles import food_key
from kitchen.convert import from_base, to_base
from kitchen.db import connect
from kitchen.quantities import parse_quantity
from kitchen.recipes import RecipeInvalid, RecipeNotFound

# Always assumed to be at hand; never worth a shopping-list line.
ALWAYS_HAVE = frozenset({"water", "מים", "ice", "קרח"})


def _qty(value: Any) -> float | None:
    if value in (None, ""):
        return None
    q = parse_quantity(value)
    if q.value is None:
        raise RecipeInvalid("quantity must be a number")
    return q.value


def _day(value: Any) -> str | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError:
        raise RecipeInvalid("expires_on must be YYYY-MM-DD")


def _row(r) -> dict:
    expires = r["expires_on"]
    days_left = (date.fromisoformat(expires) - date.today()).days if expires else None
    return {
        "id": r["id"], "food": r["food"], "quantity": r["quantity_value"], "unit": r["unit"],
        "staple": bool(r["staple"]), "expires_on": expires, "days_left": days_left,
        "note": r["note"], "updated_at": r["updated_at"],
    }


def list_items() -> list[dict]:
    with connect() as conn:
        return [_row(r) for r in conn.execute(
            "SELECT * FROM pantry_items ORDER BY expires_on IS NULL, expires_on, food COLLATE NOCASE"
        )]


def matching(food: str, items: list[dict] | None = None) -> dict | None:
    """The pantry item that stands in for ``food``, if any.

    Exact food match first, then whole-word containment either way, so a
    pantry "olive oil" covers "extra virgin olive oil" and "eggs" covers "egg".
    """
    key = food_key(food)
    if not key:
        return None
    pool = items if items is not None else list_items()
    best = None
    for item in pool:
        other = food_key(item["food"])
        if other == key:
            return item
        if other and (f" {other} " in f" {key} " or f" {key} " in f" {other} "):
            best = best or item
    return best


def add_item(*, food: str, quantity: Any = None, unit: str = "", staple: bool = False,
             expires_on: Any = None, note: str = "") -> int:
    food = " ".join(str(food or "").split())[:200]
    if not food:
        raise RecipeInvalid("Pantry item needs a food")
    amount = _qty(quantity)
    unit = str(unit or "")[:40]
    expires = _day(expires_on)
    existing = matching(food)
    with connect() as conn:
        if existing and food_key(existing["food"]) == food_key(food):
            merged_amount, merged_unit = existing["quantity"], existing["unit"]
            if amount is not None:
                if merged_amount is None:
                    merged_amount, merged_unit = amount, unit
                else:
                    a, fam_a = to_base(merged_amount, merged_unit)
                    b, fam_b = to_base(amount, unit)
                    if fam_a == fam_b:
                        merged_amount, merged_unit = from_base(a + b, fam_a)
                        if fam_a.startswith("unit:") or fam_a == "count":
                            merged_unit = existing["unit"] or unit
                    else:
                        merged_amount, merged_unit = amount, unit
            conn.execute(
                "UPDATE pantry_items SET quantity_value = ?, unit = ?, staple = MAX(staple, ?),"
                " expires_on = COALESCE(?, expires_on), updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')"
                " WHERE id = ?",
                (merged_amount, merged_unit, 1 if staple else 0, expires, existing["id"]),
            )
            conn.commit()
            return int(existing["id"])
        cur = conn.execute(
            "INSERT INTO pantry_items(food, quantity_value, unit, staple, expires_on, note) VALUES (?, ?, ?, ?, ?, ?)",
            (food, amount, unit, 1 if staple else 0, expires, str(note or "")[:500]),
        )
        conn.commit()
        return int(cur.lastrowid)


def update_item(item_id: int, patch: dict) -> None:
    sets: dict[str, Any] = {}
    if "food" in patch:
        food = " ".join(str(patch["food"] or "").split())[:200]
        if not food:
            raise RecipeInvalid("Pantry item needs a food")
        sets["food"] = food
    if "quantity" in patch:
        sets["quantity_value"] = _qty(patch["quantity"])
    if "unit" in patch:
        sets["unit"] = str(patch["unit"] or "")[:40]
    if "staple" in patch:
        sets["staple"] = 1 if patch["staple"] else 0
    if "expires_on" in patch:
        sets["expires_on"] = _day(patch["expires_on"])
    if "note" in patch:
        sets["note"] = str(patch["note"] or "")[:500]
    if not sets:
        return
    with connect() as conn:
        assignments = ", ".join(f"{k} = ?" for k in sets)
        cur = conn.execute(
            f"UPDATE pantry_items SET {assignments}, updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')"
            " WHERE id = ?", (*sets.values(), item_id),
        )
        if cur.rowcount == 0:
            raise RecipeNotFound(str(item_id))
        conn.commit()


def delete_item(item_id: int) -> None:
    with connect() as conn:
        cur = conn.execute("DELETE FROM pantry_items WHERE id = ?", (item_id,))
        if cur.rowcount == 0:
            raise RecipeNotFound(str(item_id))
        conn.commit()


def expiring(days: int = 3) -> list[dict]:
    limit = (date.today() + timedelta(days=days)).isoformat()
    with connect() as conn:
        return [_row(r) for r in conn.execute(
            "SELECT * FROM pantry_items WHERE expires_on IS NOT NULL AND expires_on <= ? ORDER BY expires_on",
            (limit,),
        )]


def coverage(food: str, amount: float | None, unit: str, items: list[dict]) -> tuple[str, float | None]:
    """How the pantry covers a needed amount.

    Returns ``("full", None)``, ``("partial", still_needed_in_unit)`` or
    ``("none", amount)``.
    """
    if food_key(food) in ALWAYS_HAVE:
        return "full", None
    item = matching(food, items)
    if not item:
        return "none", amount
    if item["staple"]:
        return "full", None
    if item["quantity"] is None:
        return "full", None
    if amount is None:
        return "full", None
    have, fam_have = to_base(item["quantity"], item["unit"])
    need, fam_need = to_base(amount, unit)
    if fam_have != fam_need:
        return "none", amount
    if have >= need:
        return "full", None
    remaining = need - have
    if fam_need in ("mass", "volume"):
        size = to_base(1, unit)[0] or 1
        return "partial", remaining / size
    return "partial", remaining


def deduct_recipe(recipe_id: str, servings: float | None = None) -> list[dict]:
    """Subtract a cooked recipe's ingredients from pantry quantities."""
    from kitchen.recipes import get_recipe

    recipe = get_recipe(recipe_id, servings=servings)
    items = list_items()
    changed: list[dict] = []
    with connect() as conn:
        for ing in recipe["ingredients"]:
            if ing["amount"] is None:
                continue
            item = matching(ing["food"], items)
            if not item or item["quantity"] is None:
                continue
            have, fam_have = to_base(item["quantity"], item["unit"])
            used, fam_used = to_base(ing["amount"], ing["unit"])
            if fam_have != fam_used:
                continue
            left = max(0.0, have - used)
            if left <= 0 and not item["staple"]:
                conn.execute("DELETE FROM pantry_items WHERE id = ?", (item["id"],))
                items = [i for i in items if i["id"] != item["id"]]
                changed.append({"food": item["food"], "removed": True})
                continue
            size = to_base(1, item["unit"])[0] or 1
            new_amount = left / size if fam_have in ("mass", "volume") else left
            conn.execute(
                "UPDATE pantry_items SET quantity_value = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')"
                " WHERE id = ?", (round(new_amount, 3), item["id"]),
            )
            item["quantity"] = new_amount
            changed.append({"food": item["food"], "quantity": round(new_amount, 3), "unit": item["unit"]})
        conn.commit()
    return changed
