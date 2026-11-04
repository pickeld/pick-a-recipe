"""Library endpoints: /api/kitchen/recipes, images, collections, cook log."""

from __future__ import annotations

import json

from flask import Response, jsonify, request, send_file

from kitchen import images, recipes, tokens
from kitchen.convert import SYSTEMS
from kitchen.db import connect
from kitchen.web import (
    KitchenError, arg_float, arg_int, arg_list, body, current_user, notify, protected_blueprint,
)

bp = protected_blueprint("kitchen_recipes", __name__)


def _units() -> str:
    units = request.args.get("units") or "original"
    if units not in SYSTEMS:
        raise KitchenError("units must be original, metric or us")
    return units


def manual_schema(data: dict) -> dict:
    """Editor payload → the Schema.org shape save_from_schema expects."""
    ingredients = data.get("ingredients") or []
    steps = data.get("steps") or []
    if not isinstance(ingredients, list) or not isinstance(steps, list):
        raise KitchenError("ingredients and steps must be lists")
    schema = {
        "name": data.get("name") or "",
        "description": data.get("description") or "",
        "recipeYield": str(data.get("servings") or data.get("yield_text") or ""),
        "prepTime": data.get("prep_time") or "",
        "cookTime": data.get("cook_time") or "",
        "totalTime": data.get("total_time") or "",
        "url": data.get("source_url") or "",
        "keywords": [str(t) for t in (data.get("tags") or []) if str(t).strip()],
        "recipeCuisine": data.get("cuisine") or "",
        "recipeCategory": data.get("category") or "",
        "recipeIngredients": [i for i in ingredients if isinstance(i, dict)],
        "recipeIngredient": [i for i in ingredients if isinstance(i, str)],
        "recipeInstructions": [
            {"@type": "HowToStep", "text": s.get("text") if isinstance(s, dict) else str(s)}
            for s in steps
        ],
    }
    if not schema["recipeIngredients"]:
        schema.pop("recipeIngredients")
    return schema


@bp.get("/recipes")
def list_recipes():
    return jsonify(recipes.list_recipes(
        username=current_user(),
        q=(request.args.get("q") or "")[:200],
        tags=arg_list("tags"),
        exclude_tags=arg_list("exclude_tags"),
        exclude_ingredients=arg_list("exclude_ingredients"),
        include_ingredients=arg_list("include_ingredients"),
        max_minutes=arg_int("max_time", lo=1, hi=24 * 60),
        favorites_only=request.args.get("favorites") in ("1", "true"),
        collection_id=arg_int("collection", lo=1, hi=2**31),
        sort=request.args.get("sort") or "newest",
        limit=arg_int("limit", 60, lo=1, hi=200) or 60,
        offset=arg_int("offset", 0, lo=0, hi=10**6) or 0,
    ))


@bp.post("/recipes")
def create_recipe():
    data = body()
    source = data.get("source_kind") if data.get("source_kind") in ("manual", "ai", "import") else "manual"
    recipe_id = recipes.save_from_schema(
        manual_schema(data), created_by=current_user(), source_kind=source,
    )
    notify("recipes", recipe_id=recipe_id)
    return jsonify(recipes.get_recipe(recipe_id, username=current_user())), 201


@bp.get("/recipes/<recipe_id>")
def get_recipe(recipe_id: str):
    data = recipes.get_recipe(
        recipe_id, username=current_user(), servings=arg_float("servings"), units=_units(),
    )
    data["share_links"] = tokens.share_count(data["id"])
    return jsonify(data)


@bp.patch("/recipes/<recipe_id>")
def update_recipe(recipe_id: str):
    recipes.update_recipe(recipe_id, body())
    notify("recipes", recipe_id=recipe_id)
    return jsonify(recipes.get_recipe(recipe_id, username=current_user()))


@bp.delete("/recipes/<recipe_id>")
def delete_recipe(recipe_id: str):
    recipes.delete_recipe(recipe_id)
    notify("recipes", recipe_id=recipe_id)
    return jsonify({"status": "deleted", "id": recipe_id})


@bp.put("/recipes/<recipe_id>/nutrition")
def set_nutrition(recipe_id: str):
    data = body()
    per_serving = data.get("per_serving")
    if not isinstance(per_serving, dict):
        raise KitchenError("per_serving must be an object")
    recipes.set_manual_nutrition(recipe_id, per_serving)
    return jsonify(recipes.get_recipe(recipe_id, username=current_user()))


@bp.delete("/recipes/<recipe_id>/nutrition")
def recalculate_nutrition(recipe_id: str):
    recipes.set_manual_nutrition(recipe_id, None)
    return jsonify(recipes.get_recipe(recipe_id, username=current_user()))


@bp.put("/recipes/<recipe_id>/me")
def set_my_meta(recipe_id: str):
    data = body()
    kwargs = {}
    if "favorite" in data:
        kwargs["favorite"] = bool(data["favorite"])
    if "rating" in data:
        kwargs["rating"] = data["rating"]
    if "note" in data:
        kwargs["note"] = str(data["note"] or "")
    try:
        meta = recipes.set_user_meta(current_user(), recipe_id, **kwargs)
    except (TypeError, ValueError) as exc:
        raise KitchenError(str(exc))
    return jsonify(meta)


@bp.post("/recipes/<recipe_id>/cooked")
def mark_cooked(recipe_id: str):
    data = body()
    servings = data.get("servings")
    try:
        servings = float(servings) if servings not in (None, "") else None
    except (TypeError, ValueError):
        raise KitchenError("servings must be a number")
    entry_id = recipes.log_cook(
        current_user(), recipe_id, cooked_on=data.get("cooked_on"), servings=servings,
        note=str(data.get("note") or ""),
    )
    return jsonify({"id": entry_id}), 201


@bp.get("/cook-log")
def cook_log():
    return jsonify({"entries": recipes.cook_history(
        current_user(), request.args.get("recipe") or None, arg_int("limit", 100, lo=1, hi=500) or 100,
    )})


@bp.delete("/cook-log/<int:entry_id>")
def delete_cook_entry(entry_id: int):
    recipes.delete_cook_entry(current_user(), entry_id)
    return jsonify({"status": "deleted"})


@bp.get("/stats")
def stats():
    return jsonify(recipes.cook_stats(current_user(), arg_int("year", lo=2000, hi=2100)))


@bp.get("/tags")
def tags():
    return jsonify({"tags": recipes.list_tags()})


# ===== Images =====

@bp.get("/images/<image_id>")
def get_image(image_id: str):
    with connect() as conn:
        row = conn.execute("SELECT filename FROM recipe_images WHERE id = ?", (image_id,)).fetchone()
    path = images.image_path(row["filename"]) if row else None
    if not path:
        return jsonify({"error": "Not found"}), 404
    response = send_file(path, mimetype="image/jpeg", max_age=86400)
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@bp.post("/recipes/<recipe_id>/images")
def upload_image(recipe_id: str):
    upload = request.files.get("image")
    if upload is None:
        raise KitchenError("Attach an image in the 'image' field")
    data = upload.read(images.MAX_IMAGE_BYTES + 1)
    try:
        filename = images.store_image_bytes(data)
    except images.ImageError as exc:
        raise KitchenError(str(exc))
    with connect() as conn:
        if not conn.execute("SELECT 1 FROM recipes WHERE id = ?", (recipe_id,)).fetchone():
            images.delete_image(filename)
            raise recipes.RecipeNotFound(recipe_id)
        image_id = recipes.add_image(conn, recipe_id, filename, first=request.form.get("cover") == "1")
        conn.commit()
    notify("recipes", recipe_id=recipe_id)
    return jsonify({"id": image_id, "url": recipes.image_url(image_id)}), 201


@bp.delete("/recipes/<recipe_id>/images/<image_id>")
def delete_image(recipe_id: str, image_id: str):
    recipes.remove_image(recipe_id, image_id)
    notify("recipes", recipe_id=recipe_id)
    return jsonify({"status": "deleted"})


@bp.post("/recipes/<recipe_id>/images/<image_id>/cover")
def set_cover(recipe_id: str, image_id: str):
    recipes.set_cover_image(recipe_id, image_id)
    notify("recipes", recipe_id=recipe_id)
    return jsonify({"status": "ok"})


# ===== Collections =====

@bp.get("/collections")
def list_collections():
    items = recipes.list_collections()
    for c in items:
        c["image_url"] = recipes.image_url(c.pop("image_id"))
    return jsonify({"collections": items})


@bp.post("/collections")
def create_collection():
    data = body()
    cid = recipes.create_collection(data.get("name") or "", data.get("description") or "", current_user())
    notify("collections")
    return jsonify({"id": cid}), 201


@bp.patch("/collections/<int:collection_id>")
def update_collection(collection_id: int):
    data = body()
    recipes.update_collection(collection_id, name=data.get("name"), description=data.get("description"))
    notify("collections")
    return jsonify({"status": "ok"})


@bp.delete("/collections/<int:collection_id>")
def delete_collection(collection_id: int):
    recipes.delete_collection(collection_id)
    notify("collections")
    return jsonify({"status": "deleted"})


@bp.put("/collections/<int:collection_id>/recipes/<recipe_id>")
def add_to_collection(collection_id: int, recipe_id: str):
    recipes.set_collection_membership(collection_id, recipe_id, True)
    notify("collections")
    return jsonify({"status": "ok"})


@bp.delete("/collections/<int:collection_id>/recipes/<recipe_id>")
def remove_from_collection(collection_id: int, recipe_id: str):
    recipes.set_collection_membership(collection_id, recipe_id, False)
    notify("collections")
    return jsonify({"status": "ok"})


# ===== Export and sharing =====

def _download(payload: object, filename: str) -> Response:
    response = Response(
        json.dumps(payload, ensure_ascii=False, indent=2), mimetype="application/json",
    )
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@bp.get("/recipes/<recipe_id>/export")
def export_recipe(recipe_id: str):
    schema = recipes.recipe_to_schema(recipe_id)
    slug = recipes.slugify(schema.get("name", "recipe"))
    safe = "".join(c for c in slug if c.isascii() and (c.isalnum() or c == "-")) or "recipe"
    return _download(schema, f"{safe}.json")


@bp.get("/export")
def export_library():
    with connect() as conn:
        ids = [r["id"] for r in conn.execute("SELECT id FROM recipes ORDER BY created_at")]
    return _download(
        {"format": "pick-a-recipe-library", "version": 1,
         "recipes": [recipes.recipe_to_schema(i) for i in ids]},
        "pick-a-recipe-library.json",
    )


@bp.post("/recipes/<recipe_id>/share")
def create_share(recipe_id: str):
    data = recipes.get_recipe(recipe_id)
    token = tokens.create_share_link(data["id"], current_user())
    return jsonify({"url": f"/s/{token}", "token": token}), 201


@bp.delete("/recipes/<recipe_id>/share")
def revoke_share(recipe_id: str):
    data = recipes.get_recipe(recipe_id)
    return jsonify({"revoked": tokens.revoke_share_links(data["id"])})
