"""The recipe library: save, read, scale, search and edit recipes.

A recipe is stored as rows (ingredients, steps, tags, nutrition) so it can be
searched, scaled and planned, plus the Schema.org document it came from so an
export never loses a field we did not model.
"""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
import uuid
from datetime import date
from typing import Any, Iterable

from helpers import parse_iso_duration, setup_logger
from kitchen import images
from kitchen.convert import convert_amount
from kitchen.db import connect
from kitchen.ingredients import parse_ingredient_line
from kitchen.quantities import Quantity, format_quantity, is_scalable, parse_quantity, scale
from kitchen.timers import detect_timers
from units import canonicalize_unit

logger = setup_logger(__name__)

SORTS = ("newest", "name", "rating", "quickest", "recently_cooked", "least_recent", "random")
SOURCE_KINDS = ("video", "web", "import", "manual", "ai")


class RecipeNotFound(LookupError):
    pass


class RecipeInvalid(ValueError):
    pass


# ===== Normalising incoming recipes =====

_SERVINGS = re.compile(r"(\d+(?:[.,]\d+)?)")
_HEBREW = re.compile(r"[\u0590-\u05FF]")
_LATIN = re.compile(r"[A-Za-z]")


def parse_servings(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value) if 0 < value <= 500 else None
    if isinstance(value, list):
        for item in value:
            found = parse_servings(item)
            if found:
                return found
        return None
    match = _SERVINGS.search(str(value or ""))
    if not match:
        return None
    number = float(match.group(1).replace(",", "."))
    return number if 0 < number <= 500 else None


def detect_language(*texts: str) -> str:
    sample = " ".join(t for t in texts if t)
    hebrew = len(_HEBREW.findall(sample))
    latin = len(_LATIN.findall(sample))
    if hebrew == 0 and latin == 0:
        return ""
    return "he" if hebrew >= latin else "en"


def slugify(name: str) -> str:
    text = unicodedata.normalize("NFKC", name or "").casefold()
    text = re.sub(r"[^\w\u0590-\u05FF]+", "-", text).strip("-")
    return text[:80] or "recipe"


def _as_list(value: Any) -> list:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    return [value]


def _tag_names(schema: dict) -> list[str]:
    names: list[str] = []
    for key in ("keywords", "recipeCategory", "recipeCuisine"):
        for item in _as_list(schema.get(key)):
            if isinstance(item, dict):
                item = item.get("name") or ""
            for part in str(item).split(","):
                part = " ".join(part.split()).strip()
                if part and len(part) <= 60:
                    names.append(part)
    for item in _as_list(schema.get("tags")):
        if isinstance(item, str) and item.strip():
            names.append(item.strip())
    seen: dict[str, str] = {}
    for n in names:
        seen.setdefault(n.casefold(), n)
    return list(seen.values())[:30]


def _first_text(value: Any) -> str:
    for item in _as_list(value):
        if isinstance(item, dict):
            item = item.get("name") or ""
        if str(item).strip():
            return str(item).strip()
    return ""


def normalize_ingredient(item: Any, position: int, section: str = "") -> dict | None:
    if isinstance(item, str):
        parsed = parse_ingredient_line(item)
    elif isinstance(item, dict):
        parsed = {
            "food": " ".join(str(item.get("food") or "").split()),
            "quantity": " ".join(str(item.get("quantity") or "").split()),
            "unit": " ".join(str(item.get("unit") or "").split()),
            "notes": " ".join(str(item.get("notes") or "").split()),
            "raw": " ".join(str(item.get("raw") or "").split()),
            "optional": bool(item.get("optional")),
        }
        if not parsed["food"] and parsed["raw"]:
            reparsed = parse_ingredient_line(parsed["raw"])
            parsed.update({k: v for k, v in reparsed.items() if k != "optional"})
        section = str(item.get("section") or section or "")
    else:
        return None
    if not parsed["food"]:
        return None
    unit = canonicalize_unit(parsed["unit"]) if parsed["unit"] else ""
    quantity = parse_quantity(parsed["quantity"])
    if "scalable" in (item if isinstance(item, dict) else {}):
        scalable = bool(item["scalable"]) and quantity.known  # type: ignore[index]
    else:
        scalable = is_scalable(quantity, unit, parsed["notes"], parsed["food"])
    raw = parsed["raw"] or " ".join(
        p for p in (parsed["quantity"], unit, parsed["food"], parsed["notes"]) if p
    )
    return {
        "position": position,
        "section": section[:120],
        "food": parsed["food"][:200],
        "quantity": parsed["quantity"][:40],
        "quantity_value": quantity.value,
        "quantity_max": quantity.max,
        "unit": unit[:40],
        "notes": parsed["notes"][:300],
        "raw": raw[:400],
        "optional": 1 if parsed.get("optional") else 0,
        "scalable": 1 if scalable else 0,
    }


def normalize_ingredients(schema: dict) -> list[dict]:
    structured = schema.get("recipeIngredients")
    source: Iterable[Any]
    if isinstance(structured, list) and structured:
        source = structured
    else:
        source = _as_list(schema.get("recipeIngredient"))
    out: list[dict] = []
    for item in source:
        norm = normalize_ingredient(item, len(out))
        if norm:
            out.append(norm)
    return out[:200]


def normalize_steps(schema: dict) -> list[dict]:
    steps: list[dict] = []

    def add(text: str, section: str = "") -> None:
        text = " ".join(str(text or "").split())
        if text:
            steps.append({"position": len(steps), "section": section[:120], "text": text[:4000]})

    instructions = schema.get("recipeInstructions")
    if isinstance(instructions, str):
        for line in re.split(r"\n+", instructions):
            add(re.sub(r"^\s*\d+[.)]\s*", "", line))
    for item in _as_list(instructions) if not isinstance(instructions, str) else []:
        if isinstance(item, str):
            add(item)
        elif isinstance(item, dict):
            if item.get("@type") == "HowToSection" or "itemListElement" in item:
                section = str(item.get("name") or "")
                for sub in _as_list(item.get("itemListElement")):
                    add(sub.get("text") if isinstance(sub, dict) else sub, section)
            else:
                add(item.get("text") or item.get("name") or "", str(item.get("section") or ""))
    return steps[:200]


def _minutes(*durations: str) -> int | None:
    total = 0
    for d in durations:
        if d:
            try:
                total += int(parse_iso_duration(d) or 0)
            except Exception:
                continue
    return total or None


# ===== Nutrition =====

def _quantity_for_nutrition(ing: dict) -> str:
    value = ing.get("quantity_value")
    return repr(value) if value else ""


def compute_nutrition(ingredients: list[dict], servings: float | None) -> dict | None:
    from nutrition import estimate_recipe_nutrition

    recipe = {
        "recipeIngredients": [
            {"food": i["food"], "quantity": _quantity_for_nutrition(i), "unit": i["unit"]}
            for i in ingredients
        ],
        "recipeYield": str(servings or ""),
    }
    try:
        return estimate_recipe_nutrition(recipe, servings=servings)
    except Exception as exc:
        logger.info("[Kitchen] nutrition estimate skipped: %s", exc)
        return None


def _schema_nutrition_numbers(schema: dict) -> dict | None:
    from helpers import parse_nutrition_value
    from nutrition import NUTRIENT_KEYS

    raw = schema.get("nutrition")
    if not isinstance(raw, dict):
        return None
    values = {}
    for key in NUTRIENT_KEYS:
        if raw.get(key):
            try:
                values[key] = float(parse_nutrition_value(str(raw[key])))
            except Exception:
                continue
    return values or None


def _write_nutrition(conn: sqlite3.Connection, recipe_id: str, ingredients: list[dict],
                     servings: float | None, schema: dict | None = None,
                     keep_manual: bool = True) -> None:
    if keep_manual:
        row = conn.execute(
            "SELECT source FROM recipe_nutrition WHERE recipe_id = ?", (recipe_id,)
        ).fetchone()
        if row and row["source"] == "manual":
            return
    estimate = compute_nutrition(ingredients, servings)
    if estimate:
        conn.execute(
            "INSERT INTO recipe_nutrition(recipe_id, per_serving, total, matched, usable, servings,"
            " source, breakdown, updated_at) VALUES (?, ?, ?, ?, ?, ?, 'auto', ?,"
            " strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))"
            " ON CONFLICT(recipe_id) DO UPDATE SET per_serving = excluded.per_serving,"
            " total = excluded.total, matched = excluded.matched, usable = excluded.usable,"
            " servings = excluded.servings, source = 'auto', breakdown = excluded.breakdown,"
            " updated_at = excluded.updated_at",
            (
                recipe_id, json.dumps(estimate["per_serving"]), json.dumps(estimate["total"]),
                estimate["matched"], estimate["usable"], estimate["servings"],
                json.dumps(estimate["ingredients"]),
            ),
        )
        return
    fallback = _schema_nutrition_numbers(schema or {})
    if fallback:
        count = servings or 1
        conn.execute(
            "INSERT OR REPLACE INTO recipe_nutrition(recipe_id, per_serving, total, matched, usable,"
            " servings, source, breakdown) VALUES (?, ?, ?, 0, ?, ?, 'source', '[]')",
            (
                recipe_id, json.dumps(fallback),
                json.dumps({k: round(v * count, 1) for k, v in fallback.items()}),
                len(ingredients), servings,
            ),
        )
    else:
        conn.execute("DELETE FROM recipe_nutrition WHERE recipe_id = ?", (recipe_id,))


def set_manual_nutrition(recipe_id: str, per_serving: dict | None) -> None:
    """Pin nutrition to user-entered values, or clear the pin and recalculate."""
    from nutrition import NUTRIENT_KEYS

    with connect() as conn:
        row = conn.execute("SELECT servings FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
        if not row:
            raise RecipeNotFound(recipe_id)
        if per_serving is None:
            ingredients = [dict(r) for r in conn.execute(
                "SELECT * FROM recipe_ingredients WHERE recipe_id = ? ORDER BY position", (recipe_id,)
            )]
            _write_nutrition(conn, recipe_id, ingredients, row["servings"], keep_manual=False)
        else:
            values = {}
            for key in NUTRIENT_KEYS:
                if key in per_serving and per_serving[key] not in (None, ""):
                    try:
                        number = float(per_serving[key])
                    except (TypeError, ValueError):
                        raise RecipeInvalid(f"{key} must be a number")
                    if number < 0 or number > 20000:
                        raise RecipeInvalid(f"{key} is out of range")
                    values[key] = number
            count = row["servings"] or 1
            conn.execute(
                "INSERT OR REPLACE INTO recipe_nutrition(recipe_id, per_serving, total, matched,"
                " usable, servings, source, breakdown) VALUES (?, ?, ?, 0, 0, ?, 'manual', '[]')",
                (recipe_id, json.dumps(values),
                 json.dumps({k: round(v * count, 1) for k, v in values.items()}), row["servings"]),
            )
        _touch(conn, recipe_id)
        conn.commit()


# ===== Writing =====

def _unique_slug(conn: sqlite3.Connection, name: str, recipe_id: str | None = None) -> str:
    base = slugify(name)
    slug = base
    n = 2
    while True:
        row = conn.execute("SELECT id FROM recipes WHERE slug = ?", (slug,)).fetchone()
        if not row or row["id"] == recipe_id:
            return slug
        slug = f"{base}-{n}"
        n += 1


def _set_tags(conn: sqlite3.Connection, recipe_id: str, names: list[str]) -> None:
    conn.execute("DELETE FROM recipe_tags WHERE recipe_id = ?", (recipe_id,))
    for name in names:
        name = " ".join(str(name).split())[:60]
        if not name:
            continue
        conn.execute("INSERT OR IGNORE INTO tags(name) VALUES (?)", (name,))
        tag = conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()
        conn.execute(
            "INSERT OR IGNORE INTO recipe_tags(recipe_id, tag_id) VALUES (?, ?)", (recipe_id, tag["id"])
        )
    conn.execute("DELETE FROM tags WHERE id NOT IN (SELECT tag_id FROM recipe_tags)")


def _write_ingredients(conn: sqlite3.Connection, recipe_id: str, ingredients: list[dict]) -> None:
    conn.execute("DELETE FROM recipe_ingredients WHERE recipe_id = ?", (recipe_id,))
    for i in ingredients:
        conn.execute(
            "INSERT INTO recipe_ingredients(recipe_id, position, section, food, quantity,"
            " quantity_value, quantity_max, unit, notes, raw, optional, scalable)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (recipe_id, i["position"], i["section"], i["food"], i["quantity"], i["quantity_value"],
             i["quantity_max"], i["unit"], i["notes"], i["raw"], i["optional"], i["scalable"]),
        )


def _link_ingredients(step_text: str, ingredients: list[dict]) -> list[int]:
    """Positions of ingredients whose food name appears in the step text."""
    folded = step_text.casefold()
    refs = []
    for ing in ingredients:
        food = ing["food"].casefold()
        candidates = {food}
        words = [w for w in re.split(r"[\s,]+", food) if len(w) >= 3]
        if words:
            candidates.add(words[-1])
            candidates.add(words[0])
        if any(c and re.search(rf"(?<![\w\u0590-\u05FF]){re.escape(c)}", folded) for c in candidates):
            refs.append(ing["position"])
    return refs


def _write_steps(conn: sqlite3.Connection, recipe_id: str, steps: list[dict],
                 ingredients: list[dict]) -> None:
    conn.execute("DELETE FROM recipe_steps WHERE recipe_id = ?", (recipe_id,))
    for s in steps:
        refs = s.get("ingredient_refs")
        if not isinstance(refs, list):
            refs = _link_ingredients(s["text"], ingredients)
        conn.execute(
            "INSERT INTO recipe_steps(recipe_id, position, section, text, timers, ingredient_refs)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (recipe_id, s["position"], s.get("section", ""), s["text"],
             json.dumps(detect_timers(s["text"]), ensure_ascii=False),
             json.dumps([int(r) for r in refs if isinstance(r, int)])),
        )


def _refresh_fts(conn: sqlite3.Connection, recipe_id: str) -> None:
    conn.execute("DELETE FROM recipes_fts WHERE recipe_id = ?", (recipe_id,))
    row = conn.execute("SELECT name, description FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
    if not row:
        return
    foods = " ".join(r["food"] for r in conn.execute(
        "SELECT food FROM recipe_ingredients WHERE recipe_id = ?", (recipe_id,)
    ))
    tags = " ".join(r["name"] for r in conn.execute(
        "SELECT t.name FROM tags t JOIN recipe_tags rt ON rt.tag_id = t.id WHERE rt.recipe_id = ?",
        (recipe_id,),
    ))
    conn.execute(
        "INSERT INTO recipes_fts(recipe_id, name, description, ingredients, tags) VALUES (?, ?, ?, ?, ?)",
        (recipe_id, row["name"], row["description"], foods, tags),
    )


def _touch(conn: sqlite3.Connection, recipe_id: str) -> None:
    conn.execute(
        "UPDATE recipes SET updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ?",
        (recipe_id,),
    )


def add_image(conn: sqlite3.Connection, recipe_id: str, filename: str, *, first: bool = False) -> str:
    image_id = uuid.uuid4().hex
    if first:
        conn.execute("UPDATE recipe_images SET position = position + 1 WHERE recipe_id = ?", (recipe_id,))
        position = 0
    else:
        row = conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 AS p FROM recipe_images WHERE recipe_id = ?",
            (recipe_id,),
        ).fetchone()
        position = row["p"]
    conn.execute(
        "INSERT INTO recipe_images(id, recipe_id, filename, position) VALUES (?, ?, ?, ?)",
        (image_id, recipe_id, filename, position),
    )
    return image_id


def find_existing(source_url: str = "", name: str = "") -> str | None:
    """A recipe already in the library with this source URL (or exact name)."""
    with connect() as conn:
        if source_url:
            row = conn.execute(
                "SELECT id FROM recipes WHERE source_url = ? AND source_url != '' LIMIT 1", (source_url,)
            ).fetchone()
            if row:
                return row["id"]
        if name:
            row = conn.execute(
                "SELECT id FROM recipes WHERE name = ? COLLATE NOCASE LIMIT 1", (name.strip(),)
            ).fetchone()
            if row:
                return row["id"]
    return None


def save_from_schema(
    schema: dict,
    *,
    image_path: str | None = None,
    image_bytes: bytes | None = None,
    created_by: str | None = None,
    source_kind: str = "video",
    history_id: int | None = None,
    replace_recipe_id: str | None = None,
) -> str:
    """Store a Schema.org recipe in the library and return its id.

    When ``replace_recipe_id`` is given, that recipe's content is replaced
    (keeping its id, favorites, plan entries and cook history).
    """
    if not isinstance(schema, dict):
        raise RecipeInvalid("Recipe must be an object")
    name = " ".join(str(schema.get("name") or "").split())[:200]
    if not name:
        raise RecipeInvalid("Recipe needs a name")
    ingredients = normalize_ingredients(schema)
    steps = normalize_steps(schema)
    if not ingredients and not steps:
        raise RecipeInvalid("Recipe needs ingredients or steps")

    description = " ".join(str(schema.get("description") or "").split())[:4000]
    yield_value = schema.get("recipeYield")
    yield_text = _first_text(yield_value) if not isinstance(yield_value, (int, float)) else str(yield_value)
    servings = parse_servings(schema.get("servings")) or parse_servings(yield_value)
    prep, cook, total = (str(schema.get(k) or "") for k in ("prepTime", "cookTime", "totalTime"))
    total_minutes = _minutes(total) or _minutes(prep, cook)
    source_url = str(schema.get("url") or "")[:2000]
    if source_kind not in SOURCE_KINDS:
        source_kind = "import"
    language = str(schema.get("inLanguage") or "")[:10] or detect_language(
        name, description, " ".join(s["text"] for s in steps[:5])
    )
    stored_schema = {k: v for k, v in schema.items() if k not in ("image",)}

    stored_image = None
    if image_bytes:
        try:
            stored_image = images.store_image_bytes(image_bytes)
        except images.ImageError as exc:
            logger.info("[Kitchen] image ignored for %r: %s", name, exc)
    elif image_path:
        stored_image = images.store_image_file(image_path)

    with connect() as conn:
        if replace_recipe_id:
            existing = conn.execute("SELECT id FROM recipes WHERE id = ?", (replace_recipe_id,)).fetchone()
            if not existing:
                raise RecipeNotFound(replace_recipe_id)
            recipe_id = replace_recipe_id
            conn.execute(
                "UPDATE recipes SET name = ?, slug = ?, description = ?, servings = ?, yield_text = ?,"
                " prep_time = ?, cook_time = ?, total_time = ?, total_minutes = ?, source_url = ?,"
                " language = ?, cuisine = ?, category = ?, schema_json = ?,"
                " updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ?",
                (name, _unique_slug(conn, name, recipe_id), description, servings, yield_text,
                 prep, cook, total, total_minutes, source_url, language,
                 _first_text(schema.get("recipeCuisine"))[:80], _first_text(schema.get("recipeCategory"))[:80],
                 json.dumps(stored_schema, ensure_ascii=False), recipe_id),
            )
        else:
            recipe_id = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO recipes(id, slug, name, description, servings, yield_text, prep_time,"
                " cook_time, total_time, total_minutes, source_url, source_kind, language, cuisine,"
                " category, history_id, schema_json, created_by)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (recipe_id, _unique_slug(conn, name), name, description, servings, yield_text,
                 prep, cook, total, total_minutes, source_url, source_kind, language,
                 _first_text(schema.get("recipeCuisine"))[:80], _first_text(schema.get("recipeCategory"))[:80],
                 history_id, json.dumps(stored_schema, ensure_ascii=False), created_by),
            )
        _write_ingredients(conn, recipe_id, ingredients)
        _write_steps(conn, recipe_id, steps, ingredients)
        _set_tags(conn, recipe_id, _tag_names(schema))
        _write_nutrition(conn, recipe_id, ingredients, servings, schema)
        if stored_image:
            add_image(conn, recipe_id, stored_image, first=True)
        _refresh_fts(conn, recipe_id)
        conn.commit()
    logger.info("[Kitchen] saved %r (%d ingredients, %d steps)", name, len(ingredients), len(steps))
    return recipe_id


_EDITABLE = {
    "name": 200, "description": 4000, "yield_text": 200, "prep_time": 20, "cook_time": 20,
    "total_time": 20, "source_url": 2000, "cuisine": 80, "category": 80, "language": 10,
}


def update_recipe(recipe_id: str, patch: dict) -> None:
    """Apply an editor patch. Only the fields listed here can change."""
    if not isinstance(patch, dict):
        raise RecipeInvalid("Patch must be an object")
    with connect() as conn:
        row = conn.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
        if not row:
            raise RecipeNotFound(recipe_id)
        sets: dict[str, Any] = {}
        for key, limit in _EDITABLE.items():
            if key in patch:
                sets[key] = " ".join(str(patch[key] or "").split())[:limit]
        if "name" in sets:
            if not sets["name"]:
                raise RecipeInvalid("Recipe needs a name")
            sets["slug"] = _unique_slug(conn, sets["name"], recipe_id)
        servings = row["servings"]
        if "servings" in patch:
            servings = parse_servings(patch["servings"])
            sets["servings"] = servings
            if "yield_text" not in sets and servings:
                sets["yield_text"] = f"{servings:g}"
        if any(k in sets for k in ("prep_time", "cook_time", "total_time")):
            prep = sets.get("prep_time", row["prep_time"])
            cook = sets.get("cook_time", row["cook_time"])
            total = sets.get("total_time", row["total_time"])
            sets["total_minutes"] = _minutes(total) or _minutes(prep, cook)
        if sets:
            assignments = ", ".join(f"{k} = ?" for k in sets)
            conn.execute(f"UPDATE recipes SET {assignments} WHERE id = ?", (*sets.values(), recipe_id))

        ingredients = None
        if "ingredients" in patch:
            if not isinstance(patch["ingredients"], list):
                raise RecipeInvalid("Ingredients must be a list")
            ingredients = []
            for item in patch["ingredients"]:
                norm = normalize_ingredient(item, len(ingredients))
                if norm:
                    ingredients.append(norm)
            _write_ingredients(conn, recipe_id, ingredients)
        if ingredients is None:
            ingredients = [dict(r) for r in conn.execute(
                "SELECT * FROM recipe_ingredients WHERE recipe_id = ? ORDER BY position", (recipe_id,)
            )]
        if "steps" in patch:
            if not isinstance(patch["steps"], list):
                raise RecipeInvalid("Steps must be a list")
            steps = []
            for item in patch["steps"]:
                text = item.get("text") if isinstance(item, dict) else item
                text = " ".join(str(text or "").split())
                if text:
                    steps.append({
                        "position": len(steps), "text": text[:4000],
                        "section": str(item.get("section") or "")[:120] if isinstance(item, dict) else "",
                    })
            _write_steps(conn, recipe_id, steps, ingredients)
        elif "ingredients" in patch:
            steps = [dict(r) for r in conn.execute(
                "SELECT position, section, text FROM recipe_steps WHERE recipe_id = ? ORDER BY position",
                (recipe_id,),
            )]
            _write_steps(conn, recipe_id, steps, ingredients)
        if "tags" in patch:
            if not isinstance(patch["tags"], list):
                raise RecipeInvalid("Tags must be a list")
            _set_tags(conn, recipe_id, [str(t) for t in patch["tags"]][:30])
        if "ingredients" in patch or "servings" in patch:
            _write_nutrition(conn, recipe_id, ingredients, servings)
        _touch(conn, recipe_id)
        _refresh_fts(conn, recipe_id)
        conn.commit()


def delete_recipe(recipe_id: str) -> None:
    with connect() as conn:
        files = [r["filename"] for r in conn.execute(
            "SELECT filename FROM recipe_images WHERE recipe_id = ?", (recipe_id,)
        )]
        cur = conn.execute("DELETE FROM recipes WHERE id = ?", (recipe_id,))
        if cur.rowcount == 0:
            raise RecipeNotFound(recipe_id)
        conn.execute("DELETE FROM recipes_fts WHERE recipe_id = ?", (recipe_id,))
        conn.execute("DELETE FROM tags WHERE id NOT IN (SELECT tag_id FROM recipe_tags)")
        conn.commit()
    for f in files:
        images.delete_image(f)


def remove_image(recipe_id: str, image_id: str) -> None:
    with connect() as conn:
        row = conn.execute(
            "SELECT filename FROM recipe_images WHERE id = ? AND recipe_id = ?", (image_id, recipe_id)
        ).fetchone()
        if not row:
            raise RecipeNotFound(image_id)
        conn.execute("DELETE FROM recipe_images WHERE id = ?", (image_id,))
        _touch(conn, recipe_id)
        conn.commit()
    images.delete_image(row["filename"])


def set_cover_image(recipe_id: str, image_id: str) -> None:
    with connect() as conn:
        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM recipe_images WHERE recipe_id = ? ORDER BY position", (recipe_id,)
        )]
        if image_id not in ids:
            raise RecipeNotFound(image_id)
        ordered = [image_id] + [i for i in ids if i != image_id]
        for pos, iid in enumerate(ordered):
            conn.execute("UPDATE recipe_images SET position = ? WHERE id = ?", (pos, iid))
        conn.commit()


# ===== Reading =====

def image_url(image_id: str | None) -> str | None:
    return f"/api/kitchen/images/{image_id}" if image_id else None


def _scaled_ingredient(row: sqlite3.Row | dict, factor: float, units: str) -> dict:
    quantity = Quantity(row["quantity_value"], row["quantity_max"])
    unit = row["unit"]
    scaled = bool(row["scalable"]) and quantity.known and abs(factor - 1) > 1e-9
    if scaled:
        quantity = scale(quantity, factor)
    amount, amount_max, display_unit = quantity.value, quantity.max, unit
    converted = False
    if quantity.known and units in ("metric", "us"):
        amount, display_unit = convert_amount(quantity.value, unit, units)  # type: ignore[arg-type]
        if quantity.max is not None:
            amount_max, _ = convert_amount(quantity.max, unit, units)
        converted = display_unit != unit or amount != quantity.value
    if quantity.known and (scaled or converted):
        display = format_quantity(Quantity(amount, amount_max), display_unit)
    else:
        display = row["quantity"]
    return {
        "id": row["id"],
        "position": row["position"],
        "section": row["section"],
        "food": row["food"],
        "quantity": row["quantity"],
        "unit": unit,
        "notes": row["notes"],
        "raw": row["raw"],
        "optional": bool(row["optional"]),
        "scalable": bool(row["scalable"]),
        "amount": amount,
        "amount_max": amount_max,
        "display_quantity": display,
        "display_unit": display_unit,
    }


def _nutrition_payload(row: sqlite3.Row | None, servings: float | None) -> dict | None:
    if not row:
        return None
    per_serving = json.loads(row["per_serving"] or "{}")
    total = {k: round(v * servings, 1) for k, v in per_serving.items()} if servings else json.loads(row["total"] or "{}")
    return {
        "per_serving": per_serving,
        "total": total,
        "matched": row["matched"],
        "usable": row["usable"],
        "source": row["source"],
        "breakdown": json.loads(row["breakdown"] or "[]"),
    }


def user_meta(conn: sqlite3.Connection, username: str | None, recipe_id: str) -> dict:
    meta = {"favorite": False, "rating": None, "note": "", "cook_count": 0, "last_cooked": None}
    if not username:
        return meta
    row = conn.execute(
        "SELECT favorite, rating, note FROM user_recipe_meta WHERE username = ? AND recipe_id = ?",
        (username, recipe_id),
    ).fetchone()
    if row:
        meta.update(favorite=bool(row["favorite"]), rating=row["rating"], note=row["note"])
    log = conn.execute(
        "SELECT COUNT(*) AS n, MAX(cooked_on) AS last FROM cook_log WHERE username = ? AND recipe_id = ?",
        (username, recipe_id),
    ).fetchone()
    meta.update(cook_count=log["n"], last_cooked=log["last"])
    return meta


def get_recipe(recipe_id: str, *, username: str | None = None, servings: float | None = None,
               units: str = "original") -> dict:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM recipes WHERE id = ? OR slug = ?", (recipe_id, recipe_id)
        ).fetchone()
        if not row:
            raise RecipeNotFound(recipe_id)
        rid = row["id"]
        base = row["servings"]
        target = servings if servings and servings > 0 else base
        factor = (target / base) if (base and target) else 1.0
        ingredients = [
            _scaled_ingredient(r, factor, units)
            for r in conn.execute(
                "SELECT * FROM recipe_ingredients WHERE recipe_id = ? ORDER BY position", (rid,)
            )
        ]
        steps = [
            {
                "position": r["position"], "section": r["section"], "text": r["text"],
                "timers": json.loads(r["timers"] or "[]"),
                "ingredient_refs": json.loads(r["ingredient_refs"] or "[]"),
            }
            for r in conn.execute(
                "SELECT * FROM recipe_steps WHERE recipe_id = ? ORDER BY position", (rid,)
            )
        ]
        tags = [r["name"] for r in conn.execute(
            "SELECT t.name FROM tags t JOIN recipe_tags rt ON rt.tag_id = t.id"
            " WHERE rt.recipe_id = ? ORDER BY t.name COLLATE NOCASE", (rid,)
        )]
        imgs = [
            {"id": r["id"], "url": image_url(r["id"])}
            for r in conn.execute(
                "SELECT id FROM recipe_images WHERE recipe_id = ? ORDER BY position", (rid,)
            )
        ]
        nutrition = _nutrition_payload(
            conn.execute("SELECT * FROM recipe_nutrition WHERE recipe_id = ?", (rid,)).fetchone(), target
        )
        collections = [
            {"id": r["id"], "name": r["name"]}
            for r in conn.execute(
                "SELECT c.id, c.name FROM collections c JOIN collection_recipes cr"
                " ON cr.collection_id = c.id WHERE cr.recipe_id = ? ORDER BY c.name", (rid,)
            )
        ]
        meta = user_meta(conn, username, rid)
    return {
        "id": rid,
        "slug": row["slug"],
        "name": row["name"],
        "description": row["description"],
        "servings": target,
        "base_servings": base,
        "yield_text": row["yield_text"],
        "prep_time": row["prep_time"],
        "cook_time": row["cook_time"],
        "total_time": row["total_time"],
        "total_minutes": row["total_minutes"],
        "source_url": row["source_url"],
        "source_kind": row["source_kind"],
        "language": row["language"],
        "cuisine": row["cuisine"],
        "category": row["category"],
        "tags": tags,
        "images": imgs,
        "image_url": imgs[0]["url"] if imgs else None,
        "ingredients": ingredients,
        "steps": steps,
        "nutrition": nutrition,
        "collections": collections,
        "user": meta,
        "units": units,
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _fts_query(text: str) -> str:
    terms = [t for t in re.split(r"[^\w\u0590-\u05FF]+", text or "") if t]
    return " ".join(f'"{t}"*' for t in terms[:10])


def list_recipes(
    *,
    username: str | None = None,
    q: str = "",
    tags: list[str] | None = None,
    exclude_tags: list[str] | None = None,
    exclude_ingredients: list[str] | None = None,
    include_ingredients: list[str] | None = None,
    max_minutes: int | None = None,
    favorites_only: bool = False,
    collection_id: int | None = None,
    sort: str = "newest",
    limit: int = 60,
    offset: int = 0,
) -> dict:
    where: list[str] = []
    params: list[Any] = []
    joins = ""
    fts = _fts_query(q)
    if fts:
        where.append("r.id IN (SELECT recipe_id FROM recipes_fts WHERE recipes_fts MATCH ?)")
        params.append(fts)
    for tag in tags or []:
        where.append(
            "r.id IN (SELECT rt.recipe_id FROM recipe_tags rt JOIN tags t ON t.id = rt.tag_id"
            " WHERE t.name = ? COLLATE NOCASE)"
        )
        params.append(tag)
    for tag in exclude_tags or []:
        where.append(
            "r.id NOT IN (SELECT rt.recipe_id FROM recipe_tags rt JOIN tags t ON t.id = rt.tag_id"
            " WHERE t.name = ? COLLATE NOCASE)"
        )
        params.append(tag)
    for food in exclude_ingredients or []:
        where.append("r.id NOT IN (SELECT recipe_id FROM recipe_ingredients WHERE food LIKE ? ESCAPE '\\')")
        params.append(f"%{_like_escape(food)}%")
    for food in include_ingredients or []:
        where.append("r.id IN (SELECT recipe_id FROM recipe_ingredients WHERE food LIKE ? ESCAPE '\\')")
        params.append(f"%{_like_escape(food)}%")
    if max_minutes:
        where.append("r.total_minutes IS NOT NULL AND r.total_minutes <= ?")
        params.append(int(max_minutes))
    if collection_id:
        joins += " JOIN collection_recipes cr ON cr.recipe_id = r.id AND cr.collection_id = ?"
        params.insert(0, int(collection_id))
    meta_join = (
        " LEFT JOIN user_recipe_meta m ON m.recipe_id = r.id AND m.username = ?"
        " LEFT JOIN (SELECT recipe_id, MAX(cooked_on) AS last_cooked, COUNT(*) AS cook_count"
        " FROM cook_log WHERE username = ? GROUP BY recipe_id) cl ON cl.recipe_id = r.id"
    )
    meta_params = [username or "", username or ""]
    if favorites_only:
        where.append("m.favorite = 1")
    order = {
        "newest": "r.created_at DESC",
        "name": "r.name COLLATE NOCASE ASC",
        "rating": "COALESCE(m.rating, 0) DESC, r.name COLLATE NOCASE",
        "quickest": "r.total_minutes IS NULL, r.total_minutes ASC",
        "recently_cooked": "cl.last_cooked IS NULL, cl.last_cooked DESC",
        "least_recent": "cl.last_cooked IS NOT NULL, cl.last_cooked ASC",
        "random": "RANDOM()",
    }.get(sort, "r.created_at DESC")
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    base_sql = f"FROM recipes r{meta_join}{joins}{where_sql}"
    all_params = meta_params + params
    with connect() as conn:
        total = conn.execute(f"SELECT COUNT(*) {base_sql}", all_params).fetchone()[0]
        rows = conn.execute(
            "SELECT r.id, r.slug, r.name, r.description, r.servings, r.total_minutes, r.created_at,"
            " r.source_kind, r.language, m.favorite, m.rating, cl.last_cooked, cl.cook_count,"
            " (SELECT id FROM recipe_images i WHERE i.recipe_id = r.id ORDER BY position LIMIT 1) AS image_id,"
            " (SELECT per_serving FROM recipe_nutrition n WHERE n.recipe_id = r.id) AS per_serving"
            f" {base_sql} ORDER BY {order} LIMIT ? OFFSET ?",
            all_params + [max(1, min(int(limit), 200)), max(0, int(offset))],
        ).fetchall()
        ids = [r["id"] for r in rows]
        tag_map: dict[str, list[str]] = {i: [] for i in ids}
        if ids:
            marks = ",".join("?" * len(ids))
            for t in conn.execute(
                f"SELECT rt.recipe_id, t.name FROM recipe_tags rt JOIN tags t ON t.id = rt.tag_id"
                f" WHERE rt.recipe_id IN ({marks}) ORDER BY t.name COLLATE NOCASE", ids
            ):
                tag_map[t["recipe_id"]].append(t["name"])
    items = []
    for r in rows:
        per_serving = json.loads(r["per_serving"]) if r["per_serving"] else {}
        items.append({
            "id": r["id"],
            "slug": r["slug"],
            "name": r["name"],
            "description": r["description"][:240],
            "servings": r["servings"],
            "total_minutes": r["total_minutes"],
            "image_url": image_url(r["image_id"]),
            "tags": tag_map.get(r["id"], []),
            "favorite": bool(r["favorite"]),
            "rating": r["rating"],
            "last_cooked": r["last_cooked"],
            "cook_count": r["cook_count"] or 0,
            "calories": per_serving.get("calories"),
            "source_kind": r["source_kind"],
            "language": r["language"],
            "created_at": r["created_at"],
        })
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def _like_escape(text: str) -> str:
    return str(text).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_").strip()


def list_tags() -> list[dict]:
    with connect() as conn:
        return [
            {"name": r["name"], "count": r["n"]}
            for r in conn.execute(
                "SELECT t.name, COUNT(rt.recipe_id) AS n FROM tags t JOIN recipe_tags rt"
                " ON rt.tag_id = t.id GROUP BY t.id ORDER BY n DESC, t.name COLLATE NOCASE"
            )
        ]


# ===== Per-user data =====

def set_user_meta(username: str, recipe_id: str, *, favorite: bool | None = None,
                  rating: int | None | str = "unset", note: str | None = None) -> dict:
    with connect() as conn:
        if not conn.execute("SELECT 1 FROM recipes WHERE id = ?", (recipe_id,)).fetchone():
            raise RecipeNotFound(recipe_id)
        conn.execute(
            "INSERT OR IGNORE INTO user_recipe_meta(username, recipe_id) VALUES (?, ?)",
            (username, recipe_id),
        )
        if favorite is not None:
            conn.execute(
                "UPDATE user_recipe_meta SET favorite = ? WHERE username = ? AND recipe_id = ?",
                (1 if favorite else 0, username, recipe_id),
            )
        if rating != "unset":
            if rating is not None:
                rating = int(rating)  # type: ignore[arg-type]
                if not 1 <= rating <= 5:
                    raise RecipeInvalid("Rating must be between 1 and 5")
            conn.execute(
                "UPDATE user_recipe_meta SET rating = ? WHERE username = ? AND recipe_id = ?",
                (rating, username, recipe_id),
            )
        if note is not None:
            conn.execute(
                "UPDATE user_recipe_meta SET note = ? WHERE username = ? AND recipe_id = ?",
                (str(note)[:4000], username, recipe_id),
            )
        conn.execute(
            "UPDATE user_recipe_meta SET updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')"
            " WHERE username = ? AND recipe_id = ?", (username, recipe_id),
        )
        conn.commit()
        return user_meta(conn, username, recipe_id)


def log_cook(username: str, recipe_id: str, *, cooked_on: str | None = None,
             servings: float | None = None, note: str = "") -> int:
    day = cooked_on or date.today().isoformat()
    try:
        date.fromisoformat(day)
    except ValueError:
        raise RecipeInvalid("cooked_on must be YYYY-MM-DD")
    with connect() as conn:
        if not conn.execute("SELECT 1 FROM recipes WHERE id = ?", (recipe_id,)).fetchone():
            raise RecipeNotFound(recipe_id)
        cur = conn.execute(
            "INSERT INTO cook_log(username, recipe_id, cooked_on, servings, note) VALUES (?, ?, ?, ?, ?)",
            (username, recipe_id, day, servings, str(note or "")[:1000]),
        )
        conn.commit()
        return int(cur.lastrowid)


def cook_history(username: str, recipe_id: str | None = None, limit: int = 100) -> list[dict]:
    sql = (
        "SELECT c.id, c.recipe_id, r.name, c.cooked_on, c.servings, c.note FROM cook_log c"
        " JOIN recipes r ON r.id = c.recipe_id WHERE c.username = ?"
    )
    params: list[Any] = [username]
    if recipe_id:
        sql += " AND c.recipe_id = ?"
        params.append(recipe_id)
    sql += " ORDER BY c.cooked_on DESC, c.id DESC LIMIT ?"
    params.append(limit)
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, params)]


def delete_cook_entry(username: str, entry_id: int) -> None:
    with connect() as conn:
        cur = conn.execute("DELETE FROM cook_log WHERE id = ? AND username = ?", (entry_id, username))
        if cur.rowcount == 0:
            raise RecipeNotFound(str(entry_id))
        conn.commit()


def cook_stats(username: str, year: int | None = None) -> dict:
    year = year or date.today().year
    prefix = f"{year:04d}-"
    with connect() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM cook_log WHERE username = ? AND cooked_on LIKE ?", (username, prefix + "%")
        ).fetchone()[0]
        distinct = conn.execute(
            "SELECT COUNT(DISTINCT recipe_id) FROM cook_log WHERE username = ? AND cooked_on LIKE ?",
            (username, prefix + "%"),
        ).fetchone()[0]
        top = [dict(r) for r in conn.execute(
            "SELECT c.recipe_id, r.name, COUNT(*) AS times FROM cook_log c JOIN recipes r ON r.id = c.recipe_id"
            " WHERE c.username = ? AND c.cooked_on LIKE ? GROUP BY c.recipe_id ORDER BY times DESC LIMIT 10",
            (username, prefix + "%"),
        )]
        by_month = {m: 0 for m in range(1, 13)}
        for r in conn.execute(
            "SELECT CAST(substr(cooked_on, 6, 2) AS INTEGER) AS m, COUNT(*) AS n FROM cook_log"
            " WHERE username = ? AND cooked_on LIKE ? GROUP BY m", (username, prefix + "%"),
        ):
            by_month[r["m"]] = r["n"]
        library = conn.execute("SELECT COUNT(*) FROM recipes").fetchone()[0]
        new_this_year = conn.execute(
            "SELECT COUNT(*) FROM recipes WHERE created_at LIKE ?", (prefix + "%",)
        ).fetchone()[0]
    return {
        "year": year, "times_cooked": total, "distinct_recipes": distinct, "top": top,
        "by_month": [by_month[m] for m in range(1, 13)],
        "library_size": library, "added_this_year": new_this_year,
    }


# ===== Collections =====

def list_collections() -> list[dict]:
    with connect() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT c.id, c.name, c.description, COUNT(cr.recipe_id) AS count,"
            " (SELECT i.id FROM collection_recipes x JOIN recipe_images i ON i.recipe_id = x.recipe_id"
            "  WHERE x.collection_id = c.id ORDER BY x.position, i.position LIMIT 1) AS image_id"
            " FROM collections c LEFT JOIN collection_recipes cr ON cr.collection_id = c.id"
            " GROUP BY c.id ORDER BY c.name COLLATE NOCASE"
        )]


def create_collection(name: str, description: str = "", created_by: str | None = None) -> int:
    name = " ".join(str(name or "").split())[:120]
    if not name:
        raise RecipeInvalid("Collection needs a name")
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO collections(name, description, created_by) VALUES (?, ?, ?)",
            (name, str(description or "")[:1000], created_by),
        )
        conn.commit()
        return int(cur.lastrowid)


def update_collection(collection_id: int, *, name: str | None = None, description: str | None = None) -> None:
    with connect() as conn:
        if not conn.execute("SELECT 1 FROM collections WHERE id = ?", (collection_id,)).fetchone():
            raise RecipeNotFound(str(collection_id))
        if name is not None:
            name = " ".join(name.split())[:120]
            if not name:
                raise RecipeInvalid("Collection needs a name")
            conn.execute("UPDATE collections SET name = ? WHERE id = ?", (name, collection_id))
        if description is not None:
            conn.execute("UPDATE collections SET description = ? WHERE id = ?", (description[:1000], collection_id))
        conn.commit()


def delete_collection(collection_id: int) -> None:
    with connect() as conn:
        cur = conn.execute("DELETE FROM collections WHERE id = ?", (collection_id,))
        if cur.rowcount == 0:
            raise RecipeNotFound(str(collection_id))
        conn.commit()


def set_collection_membership(collection_id: int, recipe_id: str, member: bool) -> None:
    with connect() as conn:
        if not conn.execute("SELECT 1 FROM collections WHERE id = ?", (collection_id,)).fetchone():
            raise RecipeNotFound(str(collection_id))
        if not conn.execute("SELECT 1 FROM recipes WHERE id = ?", (recipe_id,)).fetchone():
            raise RecipeNotFound(recipe_id)
        if member:
            conn.execute(
                "INSERT OR IGNORE INTO collection_recipes(collection_id, recipe_id, position)"
                " VALUES (?, ?, (SELECT COALESCE(MAX(position), -1) + 1 FROM collection_recipes"
                " WHERE collection_id = ?))", (collection_id, recipe_id, collection_id),
            )
        else:
            conn.execute(
                "DELETE FROM collection_recipes WHERE collection_id = ? AND recipe_id = ?",
                (collection_id, recipe_id),
            )
        conn.commit()


# ===== Export =====

def _iso_or_empty(value: str) -> str:
    return value or ""


def recipe_to_schema(recipe_id: str) -> dict:
    """Schema.org Recipe for export, merged over the document we stored."""
    data = get_recipe(recipe_id)
    with connect() as conn:
        row = conn.execute("SELECT schema_json FROM recipes WHERE id = ?", (data["id"],)).fetchone()
    try:
        schema = json.loads(row["schema_json"] or "{}")
    except (TypeError, ValueError):
        schema = {}
    schema.update({
        "@context": "https://schema.org",
        "@type": "Recipe",
        "name": data["name"],
        "description": data["description"],
        "recipeYield": data["yield_text"] or (f"{data['base_servings']:g}" if data["base_servings"] else ""),
        "prepTime": _iso_or_empty(data["prep_time"]),
        "cookTime": _iso_or_empty(data["cook_time"]),
        "totalTime": _iso_or_empty(data["total_time"]),
        "url": data["source_url"],
        "keywords": ", ".join(data["tags"]),
        "recipeIngredient": [i["raw"] for i in data["ingredients"]],
        "recipeIngredients": [
            {"food": i["food"], "quantity": i["quantity"], "unit": i["unit"], "notes": i["notes"],
             "raw": i["raw"], "optional": i["optional"], "section": i["section"]}
            for i in data["ingredients"]
        ],
        "recipeInstructions": [{"@type": "HowToStep", "text": s["text"]} for s in data["steps"]],
    })
    if data["language"]:
        schema["inLanguage"] = data["language"]
    if data["nutrition"]:
        from nutrition import schema_nutrition
        schema["nutrition"] = schema_nutrition(data["nutrition"]["per_serving"])
    for key in [k for k, v in schema.items() if v in ("", None, [])]:
        schema.pop(key)
    return schema
