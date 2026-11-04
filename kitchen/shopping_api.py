"""Shopping list endpoints: /api/kitchen/shopping."""

from __future__ import annotations

from flask import jsonify, request

from kitchen import aisles, shopping
from kitchen.web import KitchenError, body, notify, protected_blueprint

bp = protected_blueprint("kitchen_shopping", __name__)


def _list_id(raw) -> int:
    if raw in (None, "", "default"):
        return shopping.default_list_id()
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise KitchenError("Unknown list")


def _payload(list_id: int) -> dict:
    items = shopping.list_items(list_id)
    visible = [i for i in items if not i["excluded"]]
    return {
        "list_id": list_id,
        "groups": shopping.grouped([i for i in visible if not i["checked"]]),
        "checked": [i for i in visible if i["checked"]],
        "excluded": [i for i in items if i["excluded"]],
        "aisles": list(aisles.AISLES),
    }


@bp.get("/shopping/lists")
def lists():
    return jsonify({"lists": shopping.list_lists()})


@bp.post("/shopping/lists")
def create_list():
    list_id = shopping.create_list(body().get("name") or "")
    notify("shopping")
    return jsonify({"id": list_id}), 201


@bp.patch("/shopping/lists/<int:list_id>")
def rename_list(list_id: int):
    shopping.rename_list(list_id, body().get("name") or "")
    notify("shopping")
    return jsonify({"status": "ok"})


@bp.delete("/shopping/lists/<int:list_id>")
def delete_list(list_id: int):
    shopping.delete_list(list_id)
    notify("shopping")
    return jsonify({"status": "deleted"})


@bp.get("/shopping")
def get_list():
    return jsonify(_payload(_list_id(request.args.get("list"))))


@bp.post("/shopping/items")
def add_item():
    data = body()
    list_id = _list_id(data.get("list_id"))
    item_id = shopping.add_item(
        list_id, food=data.get("food") or "", quantity=data.get("quantity"),
        unit=data.get("unit") or "", note=data.get("note") or "",
    )
    notify("shopping", list_id=list_id)
    return jsonify({"id": item_id}), 201


@bp.patch("/shopping/items/<int:item_id>")
def update_item(item_id: int):
    item = shopping.update_item(item_id, body())
    if not item.get("stale"):
        notify("shopping", list_id=item["list_id"])
    return jsonify(item)


@bp.delete("/shopping/items/<int:item_id>")
def delete_item(item_id: int):
    shopping.delete_item(item_id)
    notify("shopping")
    return jsonify({"status": "deleted"})


@bp.post("/shopping/sync")
def sync():
    """Replay edits queued while offline, oldest first. Each is applied or skipped."""
    data = body()
    ops = data.get("ops")
    if not isinstance(ops, list) or len(ops) > 500:
        raise KitchenError("ops must be a list of at most 500 edits")
    results = []
    for op in ops:
        if not isinstance(op, dict):
            continue
        kind = op.get("op")
        try:
            if kind == "update":
                item = shopping.update_item(int(op["id"]), op.get("patch") or {})
                results.append({"id": op["id"], "ok": True, "stale": bool(item.get("stale"))})
            elif kind == "delete":
                shopping.delete_item(int(op["id"]))
                results.append({"id": op["id"], "ok": True})
            elif kind == "add":
                new_id = shopping.add_item(
                    _list_id(op.get("list_id")), food=op.get("food") or "",
                    quantity=op.get("quantity"), unit=op.get("unit") or "",
                )
                results.append({"temp_id": op.get("temp_id"), "id": new_id, "ok": True})
            else:
                results.append({"ok": False, "error": "unknown op"})
        except Exception as exc:
            results.append({"id": op.get("id"), "ok": False, "error": str(exc)[:200]})
    notify("shopping")
    return jsonify({"results": results})


@bp.post("/shopping/generate")
def generate():
    data = body()
    list_id = _list_id(data.get("list_id"))
    if data.get("recipe_id"):
        servings = data.get("servings")
        try:
            servings = float(servings) if servings not in (None, "") else None
        except (TypeError, ValueError):
            raise KitchenError("servings must be a number")
        only = data.get("only_foods")
        only_foods = {aisles.food_key(f) for f in only if isinstance(f, str)} if isinstance(only, list) else None
        result = shopping.add_recipe(list_id, data["recipe_id"], servings, only_foods=only_foods)
    elif data.get("start") and data.get("end"):
        result = shopping.generate_from_plan(list_id, data["start"], data["end"])
    else:
        raise KitchenError("Give a recipe_id, or a start and end date from the plan")
    notify("shopping", list_id=list_id)
    return jsonify({**result, **_payload(list_id)})


@bp.post("/shopping/clear")
def clear():
    data = body()
    list_id = _list_id(data.get("list_id"))
    removed = shopping.clear(list_id, checked_only=data.get("all") is not True)
    notify("shopping", list_id=list_id)
    return jsonify({"removed": removed})
