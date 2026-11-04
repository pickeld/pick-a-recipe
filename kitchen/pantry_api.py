"""Pantry endpoints: /api/kitchen/pantry."""

from __future__ import annotations

from flask import jsonify

from kitchen import pantry
from kitchen.web import arg_int, body, notify, protected_blueprint

bp = protected_blueprint("kitchen_pantry", __name__)


@bp.get("/pantry")
def list_items():
    return jsonify({"items": pantry.list_items(), "expiring": pantry.expiring(arg_int("days", 3, lo=0, hi=60) or 3)})


@bp.post("/pantry")
def add_item():
    data = body()
    item_id = pantry.add_item(
        food=data.get("food") or "", quantity=data.get("quantity"), unit=data.get("unit") or "",
        staple=bool(data.get("staple")), expires_on=data.get("expires_on"), note=data.get("note") or "",
    )
    notify("pantry")
    return jsonify({"id": item_id}), 201


@bp.patch("/pantry/<int:item_id>")
def update_item(item_id: int):
    pantry.update_item(item_id, body())
    notify("pantry")
    return jsonify({"status": "ok"})


@bp.delete("/pantry/<int:item_id>")
def delete_item(item_id: int):
    pantry.delete_item(item_id)
    notify("pantry")
    return jsonify({"status": "deleted"})
