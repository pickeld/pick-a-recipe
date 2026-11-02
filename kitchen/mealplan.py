"""The shared meal plan.

An entry is one dish in one slot on one day, for ``servings`` people. A
leftover entry points at the meal it comes from: it adds no shopping of its
own, but the original meal is cooked big enough to cover it.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any

from kitchen.db import connect
from kitchen.recipes import RecipeInvalid, RecipeNotFound, image_url

SLOTS = ("breakfast", "lunch", "dinner", "snack")
MAX_RANGE_DAYS = 120

_SLOT_TIMES = {"breakfast": (8, 0), "lunch": (12, 30), "snack": (16, 0), "dinner": (19, 0)}


def parse_day(value: Any, field: str = "date") -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise RecipeInvalid(f"{field} must be YYYY-MM-DD")


def _range(start: Any, end: Any) -> tuple[date, date]:
    first, last = parse_day(start, "start"), parse_day(end, "end")
    if last < first:
        raise RecipeInvalid("end is before start")
    if (last - first).days > MAX_RANGE_DAYS:
        raise RecipeInvalid(f"Range is limited to {MAX_RANGE_DAYS} days")
    return first, last


def _servings(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise RecipeInvalid("servings must be a number")
    if not 0 < number <= 100:
        raise RecipeInvalid("servings is out of range")
    return number


def list_entries(start: Any, end: Any) -> list[dict]:
    first, last = _range(start, end)
    with connect() as conn:
        rows = conn.execute(
            "SELECT e.*, r.name AS recipe_name, r.servings AS recipe_servings, r.total_minutes,"
            " (SELECT id FROM recipe_images i WHERE i.recipe_id = r.id ORDER BY position LIMIT 1) AS image_id,"
            " n.per_serving FROM meal_plan_entries e"
            " LEFT JOIN recipes r ON r.id = e.recipe_id"
            " LEFT JOIN recipe_nutrition n ON n.recipe_id = e.recipe_id"
            " WHERE e.plan_date BETWEEN ? AND ?"
            " ORDER BY e.plan_date, CASE e.slot WHEN 'breakfast' THEN 0 WHEN 'lunch' THEN 1"
            " WHEN 'snack' THEN 2 ELSE 3 END, e.position, e.id",
            (first.isoformat(), last.isoformat()),
        ).fetchall()
    out = []
    for r in rows:
        out.append({
            "id": r["id"],
            "date": r["plan_date"],
            "slot": r["slot"],
            "position": r["position"],
            "recipe_id": r["recipe_id"],
            "title": r["recipe_name"] or r["title"],
            "custom_title": r["title"],
            "servings": r["servings"],
            "recipe_servings": r["recipe_servings"],
            "total_minutes": r["total_minutes"],
            "image_url": image_url(r["image_id"]),
            "leftover_of": r["leftover_of"],
            "note": r["note"],
            "per_serving": json.loads(r["per_serving"]) if r["per_serving"] else None,
            "created_by": r["created_by"],
        })
    return out


def add_entry(*, plan_date: Any, slot: str, recipe_id: str | None = None, title: str = "",
              servings: Any = None, note: str = "", leftover_of: int | None = None,
              created_by: str | None = None) -> int:
    day = parse_day(plan_date)
    if slot not in SLOTS:
        raise RecipeInvalid(f"slot must be one of {', '.join(SLOTS)}")
    title = " ".join(str(title or "").split())[:200]
    with connect() as conn:
        if leftover_of is not None:
            source = conn.execute(
                "SELECT recipe_id, title, servings FROM meal_plan_entries WHERE id = ?", (leftover_of,)
            ).fetchone()
            if not source:
                raise RecipeNotFound(str(leftover_of))
            recipe_id = source["recipe_id"]
            title = title or source["title"]
        if recipe_id:
            if not conn.execute("SELECT 1 FROM recipes WHERE id = ?", (recipe_id,)).fetchone():
                raise RecipeNotFound(recipe_id)
        elif not title:
            raise RecipeInvalid("An entry needs a recipe or a title")
        position = conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 FROM meal_plan_entries WHERE plan_date = ? AND slot = ?",
            (day.isoformat(), slot),
        ).fetchone()[0]
        cur = conn.execute(
            "INSERT INTO meal_plan_entries(plan_date, slot, position, recipe_id, title, servings,"
            " leftover_of, note, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (day.isoformat(), slot, position, recipe_id, title, _servings(servings),
             leftover_of, str(note or "")[:1000], created_by),
        )
        conn.commit()
        return int(cur.lastrowid)


def update_entry(entry_id: int, patch: dict) -> None:
    sets: dict[str, Any] = {}
    if "date" in patch:
        sets["plan_date"] = parse_day(patch["date"]).isoformat()
    if "slot" in patch:
        if patch["slot"] not in SLOTS:
            raise RecipeInvalid("Unknown slot")
        sets["slot"] = patch["slot"]
    if "position" in patch:
        try:
            sets["position"] = max(0, int(patch["position"]))
        except (TypeError, ValueError):
            raise RecipeInvalid("position must be a number")
    if "servings" in patch:
        sets["servings"] = _servings(patch["servings"])
    if "title" in patch:
        sets["title"] = " ".join(str(patch["title"] or "").split())[:200]
    if "note" in patch:
        sets["note"] = str(patch["note"] or "")[:1000]
    if not sets:
        return
    with connect() as conn:
        assignments = ", ".join(f"{k} = ?" for k in sets)
        cur = conn.execute(f"UPDATE meal_plan_entries SET {assignments} WHERE id = ?", (*sets.values(), entry_id))
        if cur.rowcount == 0:
            raise RecipeNotFound(str(entry_id))
        conn.commit()


def delete_entry(entry_id: int) -> None:
    with connect() as conn:
        cur = conn.execute("DELETE FROM meal_plan_entries WHERE id = ?", (entry_id,))
        if cur.rowcount == 0:
            raise RecipeNotFound(str(entry_id))
        conn.commit()


def copy_range(src_start: Any, src_end: Any, dest_start: Any, created_by: str | None = None) -> int:
    """Copy a span of days (e.g. last week) so it starts on ``dest_start``."""
    first, last = _range(src_start, src_end)
    offset = parse_day(dest_start, "dest_start") - first
    if offset.days == 0:
        return 0
    copied = 0
    id_map: dict[int, int] = {}
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM meal_plan_entries WHERE plan_date BETWEEN ? AND ? ORDER BY leftover_of IS NOT NULL, id",
            (first.isoformat(), last.isoformat()),
        ).fetchall()
        for r in rows:
            day = date.fromisoformat(r["plan_date"]) + offset
            leftover = id_map.get(r["leftover_of"]) if r["leftover_of"] else None
            cur = conn.execute(
                "INSERT INTO meal_plan_entries(plan_date, slot, position, recipe_id, title, servings,"
                " leftover_of, note, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (day.isoformat(), r["slot"], r["position"], r["recipe_id"], r["title"], r["servings"],
                 leftover, r["note"], created_by),
            )
            id_map[r["id"]] = int(cur.lastrowid)
            copied += 1
        conn.commit()
    return copied


def clear_range(start: Any, end: Any) -> int:
    first, last = _range(start, end)
    with connect() as conn:
        cur = conn.execute(
            "DELETE FROM meal_plan_entries WHERE plan_date BETWEEN ? AND ?", (first.isoformat(), last.isoformat())
        )
        conn.commit()
        return cur.rowcount


def daily_nutrition(entries: list[dict]) -> dict[str, dict]:
    """Per-person totals per day: one serving of each planned dish."""
    days: dict[str, dict] = {}
    for e in entries:
        per = e.get("per_serving")
        day = days.setdefault(e["date"], {"meals": 0, "covered": 0, "totals": {}})
        day["meals"] += 1
        if not per:
            continue
        day["covered"] += 1
        for key, value in per.items():
            day["totals"][key] = round(day["totals"].get(key, 0) + float(value), 1)
    return days


def cooking_plan(start: Any, end: Any) -> list[dict]:
    """Recipes to cook in a range, with servings that include their leftovers."""
    first, last = _range(start, end)
    with connect() as conn:
        rows = conn.execute(
            "SELECT e.id, e.recipe_id, e.servings, e.plan_date,"
            " (SELECT COALESCE(SUM(COALESCE(l.servings, 0)), 0) FROM meal_plan_entries l"
            "  WHERE l.leftover_of = e.id) AS leftover_servings,"
            " r.servings AS base_servings, r.name"
            " FROM meal_plan_entries e JOIN recipes r ON r.id = e.recipe_id"
            " WHERE e.plan_date BETWEEN ? AND ? AND e.leftover_of IS NULL",
            (first.isoformat(), last.isoformat()),
        ).fetchall()
    plan = []
    for r in rows:
        needed = (r["servings"] or r["base_servings"] or 0) + (r["leftover_servings"] or 0)
        plan.append({
            "entry_id": r["id"], "recipe_id": r["recipe_id"], "name": r["name"], "date": r["plan_date"],
            "servings": needed or None, "base_servings": r["base_servings"],
        })
    return plan


def empty_slots(start: Any, end: Any, slots: list[str]) -> list[tuple[str, str]]:
    first, last = _range(start, end)
    with connect() as conn:
        taken = {
            (r["plan_date"], r["slot"])
            for r in conn.execute(
                "SELECT plan_date, slot FROM meal_plan_entries WHERE plan_date BETWEEN ? AND ?",
                (first.isoformat(), last.isoformat()),
            )
        }
    out = []
    day = first
    while day <= last:
        for slot in slots:
            if slot in SLOTS and (day.isoformat(), slot) not in taken:
                out.append((day.isoformat(), slot))
        day += timedelta(days=1)
    return out


# ===== Calendar feed =====

def _ics_escape(text: str) -> str:
    return (
        str(text or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
        .replace("\r", "").replace("\n", "\\n")
    )


def _fold_line(line: str) -> str:
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    parts, current = [], b""
    for ch in line:
        encoded = ch.encode("utf-8")
        if len(current) + len(encoded) > 74:
            parts.append(current.decode("utf-8"))
            current = b""
        current += encoded
    parts.append(current.decode("utf-8"))
    return "\r\n ".join(parts)


def ics_feed(base_url: str, *, past_days: int = 30, future_days: int = 90) -> str:
    today = date.today()
    start, end = today - timedelta(days=past_days), today + timedelta(days=future_days)
    with connect() as conn:
        rows = conn.execute(
            "SELECT e.id, e.plan_date, e.slot, e.title, e.servings, e.note, e.leftover_of, e.recipe_id,"
            " r.name FROM meal_plan_entries e LEFT JOIN recipes r ON r.id = e.recipe_id"
            " WHERE e.plan_date BETWEEN ? AND ? ORDER BY e.plan_date",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Pick-a-Recipe//Meal plan//EN",
        "CALSCALE:GREGORIAN", "METHOD:PUBLISH", "X-WR-CALNAME:Meal plan",
    ]
    for r in rows:
        day = date.fromisoformat(r["plan_date"])
        hour, minute = _SLOT_TIMES.get(r["slot"], (12, 0))
        begin = datetime(day.year, day.month, day.day, hour, minute)
        finish = begin + timedelta(hours=1)
        title = r["name"] or r["title"] or "Meal"
        if r["leftover_of"]:
            title = f"{title} (leftovers)"
        description = []
        if r["servings"]:
            description.append(f"Servings: {r['servings']:g}")
        if r["note"]:
            description.append(r["note"])
        if r["recipe_id"]:
            description.append(f"{base_url.rstrip('/')}/recipes/{r['recipe_id']}")
        lines += [
            "BEGIN:VEVENT",
            f"UID:meal-{r['id']}@pick-a-recipe",
            f"DTSTAMP:{stamp}",
            f"DTSTART:{begin.strftime('%Y%m%dT%H%M%S')}",
            f"DTEND:{finish.strftime('%Y%m%dT%H%M%S')}",
            f"SUMMARY:{_ics_escape(title)}",
            f"CATEGORIES:{_ics_escape(r['slot'])}",
        ]
        if description:
            lines.append(f"DESCRIPTION:{_ics_escape(chr(10).join(description))}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold_line(line) for line in lines) + "\r\n"
