"""Token-authenticated endpoints: the calendar feed and shared recipes.

No session is involved. Each request carries a token from a link the user
created; an unknown token gets the same 404 as a missing recipe so a guess
learns nothing.
"""

from __future__ import annotations

from flask import Response, jsonify, request, send_file

from kitchen import images, mealplan, recipes, tokens
from kitchen.db import connect
from kitchen.web import public_blueprint

bp = public_blueprint("kitchen_public", __name__)


def _not_found():
    return jsonify({"error": "Not found"}), 404


@bp.get("/ics/<token>.ics")
def ics(token: str):
    if not tokens.ics_user(token):
        return _not_found()
    response = Response(mealplan.ics_feed(request.host_url), mimetype="text/calendar")
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Disposition"] = 'inline; filename="meal-plan.ics"'
    return response


@bp.get("/api/public/share/<token>")
def shared(token: str):
    recipe_id = tokens.shared_recipe(token)
    if not recipe_id:
        return _not_found()
    data = recipes.get_recipe(recipe_id, servings=request.args.get("servings", type=float))
    public = {k: data[k] for k in (
        "name", "description", "servings", "base_servings", "yield_text", "prep_time", "cook_time",
        "total_time", "total_minutes", "source_url", "language", "tags", "ingredients", "steps", "nutrition",
    )}
    public["images"] = [
        {"id": img["id"], "url": f"/api/public/share/{token}/images/{img['id']}"} for img in data["images"]
    ]
    public["image_url"] = public["images"][0]["url"] if public["images"] else None
    response = jsonify(public)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@bp.get("/api/public/share/<token>/images/<image_id>")
def shared_image(token: str, image_id: str):
    recipe_id = tokens.shared_recipe(token)
    if not recipe_id:
        return _not_found()
    with connect() as conn:
        row = conn.execute(
            "SELECT filename FROM recipe_images WHERE id = ? AND recipe_id = ?", (image_id, recipe_id)
        ).fetchone()
    path = images.image_path(row["filename"]) if row else None
    if not path:
        return _not_found()
    response = send_file(path, mimetype="image/jpeg", max_age=3600)
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response
