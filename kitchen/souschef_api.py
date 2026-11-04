"""Sous Chef endpoints: /api/kitchen/souschef/* plus per-recipe AI helpers."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any

from flask import jsonify

from kitchen import souschef
from kitchen.recipes import save_from_schema, update_recipe, get_recipe
from kitchen.web import KitchenError, body, current_user, notify, protected_blueprint

bp = protected_blueprint("kitchen_souschef", __name__)

_busy: set[str] = set()
_busy_lock = threading.Lock()


@contextmanager
def _one_at_a_time():
    """One AI request per user at a time; they are slow and cost money."""
    user = current_user()
    with _busy_lock:
        if user in _busy:
            raise KitchenError("Sous Chef is still working on your last request", 429)
        _busy.add(user)
    try:
        yield user
    except souschef.SousChefUnavailable as exc:
        raise KitchenError(str(exc), 503) from exc
    finally:
        with _busy_lock:
            _busy.discard(user)


def _strings(value: Any, limit: int = 60) -> list[str]:
    if isinstance(value, str):
        value = [v for v in value.replace("\n", ",").split(",")]
    if not isinstance(value, list):
        return []
    out = []
    for v in value[:limit]:
        text = " ".join(str(v).split())[:80]
        if text:
            out.append(text)
    return out


def _int_or_none(value: Any, lo: int, hi: int) -> int | None:
    if value in (None, ""):
        return None
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        raise KitchenError("expected a number")


def _diets(value: Any) -> list[str]:
    return [d for d in _strings(value, 5) if d in souschef.DIETS]


@bp.get("/souschef/diets")
def diets():
    return jsonify({"diets": list(souschef.DIETS)})


@bp.post("/souschef/suggest")
def suggest():
    data = body()
    history = data.get("history") if isinstance(data.get("history"), list) else []
    use_ai = data.get("ai", True) is not False
    kwargs = dict(
        on_hand=_strings(data.get("ingredients")),
        use_pantry=bool(data.get("use_pantry")),
        message=str(data.get("message") or "")[:1000],
        history=history[-10:],
        max_minutes=_int_or_none(data.get("max_minutes"), 5, 1440),
        diets=_diets(data.get("diets")),
        tags=_strings(data.get("tags"), 10),
        lang=str(data.get("lang") or "")[:10] or None,
    )
    if not use_ai:
        return jsonify(souschef.suggest(username=current_user(), use_ai=False, **kwargs))
    with _one_at_a_time() as user:
        return jsonify(souschef.suggest(username=user, **kwargs))


@bp.post("/souschef/invent")
def invent():
    data = body()
    with _one_at_a_time() as user:
        draft = souschef.invent(
            username=user, on_hand=_strings(data.get("ingredients")),
            message=str(data.get("message") or "")[:1000], use_pantry=bool(data.get("use_pantry")),
            diets=_diets(data.get("diets")), max_minutes=_int_or_none(data.get("max_minutes"), 5, 1440),
        )
    return jsonify({"draft": draft})


@bp.post("/souschef/save-draft")
def save_draft():
    data = body()
    draft = data.get("draft")
    if not isinstance(draft, dict) or not str(draft.get("name") or "").strip():
        raise KitchenError("draft recipe required")
    draft = {k: v for k, v in draft.items() if k not in ("image", "url", "video")}
    recipe_id = save_from_schema(draft, created_by=current_user(), source_kind="ai")
    notify("recipes", id=recipe_id)
    return jsonify({"id": recipe_id}), 201


@bp.post("/souschef/substitute")
def substitute():
    data = body()
    food = " ".join(str(data.get("food") or "").split())
    if not food:
        raise KitchenError("food required")
    with _one_at_a_time():
        options = souschef.substitute(food, data.get("recipe_id") or None)
    return jsonify({"options": options})


@bp.post("/souschef/use-it-up")
def use_it_up():
    data = body()
    days = _int_or_none(data.get("days"), 0, 30)
    lang = str(data.get("lang") or "")[:10] or None
    if data.get("ai") is False:
        return jsonify(souschef.use_it_up(
            username=current_user(), days=3 if days is None else days, use_ai=False, lang=lang,
        ))
    with _one_at_a_time() as user:
        return jsonify(souschef.use_it_up(username=user, days=3 if days is None else days, lang=lang))


@bp.post("/souschef/plan-week")
def plan_week():
    data = body()
    start, end = data.get("start"), data.get("end")
    if not start or not end:
        start, end = souschef.week_bounds()
    slots = [s for s in _strings(data.get("slots"), 4) if s in ("breakfast", "lunch", "dinner", "snack")]
    with _one_at_a_time() as user:
        return jsonify(souschef.plan_week(
            username=user, start=start, end=end, slots=slots or ["dinner"],
            message=str(data.get("message") or "")[:1000], use_pantry=data.get("use_pantry", True) is not False,
        ))


@bp.post("/souschef/plan-week/apply")
def apply_plan():
    data = body()
    assignments = data.get("assignments")
    if not isinstance(assignments, list):
        raise KitchenError("assignments required")
    added = souschef.apply_plan(assignments, servings=data.get("servings"), created_by=current_user())
    notify("plan")
    return jsonify({"added": added})


@bp.post("/recipes/<recipe_id>/ai/review")
def review(recipe_id: str):
    with _one_at_a_time():
        return jsonify(souschef.review_recipe(recipe_id))


@bp.post("/recipes/<recipe_id>/ai/tags")
def tags(recipe_id: str):
    data = body()
    with _one_at_a_time():
        suggested = souschef.suggest_tags(recipe_id)
    if data.get("apply") and suggested:
        recipe = get_recipe(recipe_id)
        update_recipe(recipe["id"], {"tags": recipe["tags"] + suggested})
        notify("recipes", id=recipe["id"])
    return jsonify({"tags": suggested})


@bp.post("/recipes/<recipe_id>/ai/link-steps")
def link_steps(recipe_id: str):
    with _one_at_a_time():
        updated = souschef.link_steps(recipe_id)
    notify("recipes", id=recipe_id)
    return jsonify({"updated": updated})
