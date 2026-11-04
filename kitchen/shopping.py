"""Shopping lists: generate from the plan or a recipe, merge, group by aisle.

Edits carry the client's timestamp. An edit made offline and replayed later
only wins if it is newer than what the server already has, so a stale phone
cannot undo a check-off someone else made in the meantime.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from kitchen import aisles
from kitchen.aisles import food_key
from kitchen.convert import from_base, to_base
from kitchen.db import connect
from kitchen.recipes import RecipeInvalid, RecipeNotFound, get_recipe

DEFAULT_LIST_NAME = "Shopping list"
ALWAYS_HAVE = frozenset({"water", "מים", "ice", "קרח"})


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _client_ts(value: Any) -> str:
    if not value:
        return _now()
    text = str(value)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise RecipeInvalid("client_ts must be an ISO timestamp")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed = min(parsed.astimezone(timezone.utc), datetime.now(timezone.utc))
    return parsed.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


# ===== Lists =====

def default_list_id() -> int:
    with connect() as conn:
        row = conn.execute("SELECT id FROM shopping_lists WHERE archived = 0 ORDER BY id LIMIT 1").fetchone()
        if row:
            return int(row["id"])
        cur = conn.execute("INSERT INTO shopping_lists(name) VALUES (?)", (DEFAULT_LIST_NAME,))
        conn.commit()
        return int(cur.lastrowid)


def list_lists() -> list[dict]:
    default_list_id()
    with connect() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT l.id, l.name, l.archived, l.created_at,"
            " SUM(CASE WHEN i.checked = 0 AND i.excluded = 0 THEN 1 ELSE 0 END) AS open_items"
            " FROM shopping_lists l LEFT JOIN shopping_items i ON i.list_id = l.id"
            " WHERE l.archived = 0 GROUP BY l.id ORDER BY l.id"
        )]


def create_list(name: str) -> int:
    name = " ".join(str(name or "").split())[:120]
    if not name:
        raise RecipeInvalid("List needs a name")
    with connect() as conn:
        cur = conn.execute("INSERT INTO shopping_lists(name) VALUES (?)", (name,))
        conn.commit()
        return int(cur.lastrowid)


def rename_list(list_id: int, name: str) -> None:
    name = " ".join(str(name or "").split())[:120]
    if not name:
        raise RecipeInvalid("List needs a name")
    with connect() as conn:
        cur = conn.execute("UPDATE shopping_lists SET name = ? WHERE id = ?", (name, list_id))
        if cur.rowcount == 0:
            raise RecipeNotFound(str(list_id))
        conn.commit()


def delete_list(list_id: int) -> None:
    with connect() as conn:
        cur = conn.execute("DELETE FROM shopping_lists WHERE id = ?", (list_id,))
        if cur.rowcount == 0:
            raise RecipeNotFound(str(list_id))
        conn.commit()


def _ensure_list(conn, list_id: int) -> None:
    if not conn.execute("SELECT 1 FROM shopping_lists WHERE id = ?", (list_id,)).fetchone():
        raise RecipeNotFound(str(list_id))


# ===== Items =====

def _item(r) -> dict:
    return {
        "id": r["id"], "list_id": r["list_id"], "food": r["food"], "quantity": r["quantity_value"],
        "unit": r["unit"], "aisle": r["aisle"], "checked": bool(r["checked"]),
        "excluded": bool(r["excluded"]), "position": r["position"], "note": r["note"],
        "sources": json.loads(r["source_recipes"] or "[]"), "manual": bool(r["manual"]),
        "updated_at": r["updated_at"],
    }


def list_items(list_id: int) -> list[dict]:
    with connect() as conn:
        _ensure_list(conn, list_id)
        rows = conn.execute(
            "SELECT * FROM shopping_items WHERE list_id = ? ORDER BY checked, aisle, position, id", (list_id,)
        ).fetchall()
    return [_item(r) for r in rows]


def grouped(items: list[dict]) -> list[dict]:
    order = {a: i for i, a in enumerate(aisles.AISLES)}
    groups: dict[str, list[dict]] = {}
    for item in items:
        groups.setdefault(item["aisle"], []).append(item)
    return [
        {"aisle": a, "items": groups[a]}
        for a in sorted(groups, key=lambda a: order.get(a, len(order)))
    ]


def _merge_into(conn, list_id: int, food: str, amount: float | None, unit: str,
                sources: list[dict], *, manual: bool, excluded: bool, note: str,
                overrides: dict[str, str]) -> int:
    """Add to an open item for the same food and unit family, or insert one."""
    key = food_key(food)
    family = to_base(amount, unit)[1] if amount is not None else None
    for r in conn.execute(
        "SELECT * FROM shopping_items WHERE list_id = ? AND checked = 0 AND excluded = ?",
        (list_id, 1 if excluded else 0),
    ).fetchall():
        if food_key(r["food"]) != key:
            continue
        existing_family = to_base(r["quantity_value"], r["unit"])[1] if r["quantity_value"] is not None else None
        if amount is not None and existing_family is not None and existing_family != family:
            continue
        new_amount, new_unit = r["quantity_value"], r["unit"]
        if amount is not None:
            if new_amount is None:
                new_amount, new_unit = amount, unit
            elif r["unit"] == unit:
                new_amount = new_amount + amount
            else:
                a, fam = to_base(new_amount, r["unit"])
                b, _ = to_base(amount, unit)
                new_amount, new_unit = from_base(a + b, fam)
        merged_sources = json.loads(r["source_recipes"] or "[]")
        known = {s.get("id") for s in merged_sources}
        merged_sources += [s for s in sources if s.get("id") not in known]
        conn.execute(
            "UPDATE shopping_items SET quantity_value = ?, unit = ?, source_recipes = ?, updated_at = ? WHERE id = ?",
            (round(new_amount, 3) if new_amount is not None else None, new_unit,
             json.dumps(merged_sources, ensure_ascii=False), _now(), r["id"]),
        )
        return int(r["id"])
    position = conn.execute(
        "SELECT COALESCE(MAX(position), -1) + 1 FROM shopping_items WHERE list_id = ?", (list_id,)
    ).fetchone()[0]
    cur = conn.execute(
        "INSERT INTO shopping_items(list_id, food, quantity_value, unit, aisle, excluded, position, note,"
        " source_recipes, manual, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (list_id, food[:200], round(amount, 3) if amount is not None else None, unit[:40],
         aisles.classify(food, overrides), 1 if excluded else 0, position, note[:300],
         json.dumps(sources, ensure_ascii=False), 1 if manual else 0, _now()),
    )
    return int(cur.lastrowid)


def add_item(list_id: int, *, food: str, quantity: Any = None, unit: str = "", note: str = "") -> int:
    from kitchen.ingredients import parse_ingredient_line
    from kitchen.quantities import parse_quantity

    food = " ".join(str(food or "").split())
    if not food:
        raise RecipeInvalid("Item needs a name")
    amount = parse_quantity(quantity).value if quantity not in (None, "") else None
    if amount is None and not unit and quantity in (None, ""):
        # Quick-add: "2 kg tomatoes" typed into one box.
        parsed = parse_ingredient_line(food)
        if parsed["quantity"]:
            food, unit = parsed["food"], parsed["unit"]
            amount = parse_quantity(parsed["quantity"]).value
            note = note or parsed["notes"]
    with connect() as conn:
        _ensure_list(conn, list_id)
        item_id = _merge_into(
            conn, list_id, food, amount, str(unit or ""), [], manual=True, excluded=False,
            note=str(note or ""), overrides=aisles.load_overrides(),
        )
        conn.commit()
        return item_id


def update_item(item_id: int, patch: dict) -> dict:
    """Apply an edit unless the server already holds a newer one."""
    ts = _client_ts(patch.get("client_ts"))
    with connect() as conn:
        row = conn.execute("SELECT * FROM shopping_items WHERE id = ?", (item_id,)).fetchone()
        if not row:
            raise RecipeNotFound(str(item_id))
        if row["updated_at"] and row["updated_at"] > ts:
            return {**_item(row), "stale": True}
        sets: dict[str, Any] = {}
        if "checked" in patch:
            sets["checked"] = 1 if patch["checked"] else 0
        if "excluded" in patch:
            sets["excluded"] = 1 if patch["excluded"] else 0
        if "food" in patch:
            food = " ".join(str(patch["food"] or "").split())[:200]
            if not food:
                raise RecipeInvalid("Item needs a name")
            sets["food"] = food
        if "quantity" in patch:
            from kitchen.quantities import parse_quantity
            sets["quantity_value"] = parse_quantity(patch["quantity"]).value if patch["quantity"] not in (None, "") else None
        if "unit" in patch:
            sets["unit"] = str(patch["unit"] or "")[:40]
        if "note" in patch:
            sets["note"] = str(patch["note"] or "")[:300]
        if "aisle" in patch:
            if patch["aisle"] not in aisles.AISLES:
                raise RecipeInvalid("Unknown aisle")
            sets["aisle"] = patch["aisle"]
        sets["updated_at"] = ts
        assignments = ", ".join(f"{k} = ?" for k in sets)
        conn.execute(f"UPDATE shopping_items SET {assignments} WHERE id = ?", (*sets.values(), item_id))
        conn.commit()
        row = conn.execute("SELECT * FROM shopping_items WHERE id = ?", (item_id,)).fetchone()
    if "aisle" in patch:
        aisles.remember(row["food"], patch["aisle"])
    return _item(row)


def delete_item(item_id: int) -> None:
    with connect() as conn:
        cur = conn.execute("DELETE FROM shopping_items WHERE id = ?", (item_id,))
        if cur.rowcount == 0:
            raise RecipeNotFound(str(item_id))
        conn.commit()


def clear(list_id: int, *, checked_only: bool = True) -> int:
    with connect() as conn:
        _ensure_list(conn, list_id)
        sql = "DELETE FROM shopping_items WHERE list_id = ?"
        if checked_only:
            sql += " AND checked = 1"
        cur = conn.execute(sql, (list_id,))
        conn.commit()
        return cur.rowcount


# ===== Generating =====

def add_recipe(list_id: int, recipe_id: str, servings: float | None = None, *,
               conn=None, overrides: dict | None = None,
               only_foods: set[str] | None = None) -> dict:
    """Add a recipe's ingredients (scaled) to a list. Returns counts."""
    recipe = get_recipe(recipe_id, servings=servings)
    source = [{"id": recipe["id"], "name": recipe["name"]}]
    own = conn is None
    if own:
        conn_ctx = connect()
        conn = conn_ctx.__enter__()
    try:
        _ensure_list(conn, list_id)
        overrides = overrides if overrides is not None else aisles.load_overrides()
        added = 0
        for ing in recipe["ingredients"]:
            if only_foods is not None and food_key(ing["food"]) not in only_foods:
                continue
            if food_key(ing["food"]) in ALWAYS_HAVE:
                continue
            _merge_into(conn, list_id, ing["food"], ing["amount"], ing["unit"], source, manual=False,
                        excluded=False, note=ing["notes"] if not ing["optional"] else "optional",
                        overrides=overrides)
            added += 1
        if own:
            conn.commit()
        return {"added": added}
    finally:
        if own:
            conn_ctx.__exit__(None, None, None)


def generate_from_plan(list_id: int, start: str, end: str) -> dict:
    from kitchen.mealplan import cooking_plan

    totals = {"added": 0, "recipes": 0}
    overrides = aisles.load_overrides()
    with connect() as conn:
        _ensure_list(conn, list_id)
        for meal in cooking_plan(start, end):
            result = add_recipe(list_id, meal["recipe_id"], meal["servings"], conn=conn, overrides=overrides)
            totals["added"] += result["added"]
            totals["recipes"] += 1
        conn.commit()
    return totals
