"""Meal planner endpoints: /api/kitchen/plan."""

from __future__ import annotations

from flask import jsonify, request

from kitchen import mealplan, tokens
from kitchen.web import KitchenError, body, current_user, notify, protected_blueprint

bp = protected_blueprint("kitchen_plan", __name__)


@bp.get("/plan")
def list_plan():
    start, end = request.args.get("start"), request.args.get("end")
    if not start or not end:
        raise KitchenError("start and end are required")
    entries = mealplan.list_entries(start, end)
    return jsonify({"entries": entries, "nutrition": mealplan.daily_nutrition(entries)})


@bp.post("/plan")
def add_entry():
    data = body()
    leftover_of = data.get("leftover_of")
    entry_id = mealplan.add_entry(
        plan_date=data.get("date"),
        slot=data.get("slot") or "dinner",
        recipe_id=data.get("recipe_id") or None,
        title=data.get("title") or "",
        servings=data.get("servings"),
        note=data.get("note") or "",
        leftover_of=int(leftover_of) if leftover_of else None,
        created_by=current_user(),
    )
    notify("plan")
    return jsonify({"id": entry_id}), 201


@bp.patch("/plan/<int:entry_id>")
def update_entry(entry_id: int):
    mealplan.update_entry(entry_id, body())
    notify("plan")
    return jsonify({"status": "ok"})


@bp.delete("/plan/<int:entry_id>")
def delete_entry(entry_id: int):
    mealplan.delete_entry(entry_id)
    notify("plan")
    return jsonify({"status": "deleted"})


@bp.post("/plan/copy")
def copy_range():
    data = body()
    copied = mealplan.copy_range(data.get("start"), data.get("end"), data.get("dest_start"), current_user())
    notify("plan")
    return jsonify({"copied": copied})


@bp.post("/plan/clear")
def clear_range():
    data = body()
    removed = mealplan.clear_range(data.get("start"), data.get("end"))
    notify("plan")
    return jsonify({"removed": removed})


@bp.get("/plan/ics")
def ics_status():
    return jsonify({"enabled": tokens.has_ics_token(current_user())})


@bp.post("/plan/ics")
def ics_rotate():
    token = tokens.rotate_ics_token(current_user())
    return jsonify({"url": f"{request.host_url.rstrip('/')}/ics/{token}.ics"})


@bp.delete("/plan/ics")
def ics_revoke():
    tokens.revoke_ics_token(current_user())
    return jsonify({"enabled": False})
