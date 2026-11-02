"""Flask wiring for the kitchen blueprints.

The app owns authentication; it hands us its identity lookups at startup so
these modules never import ui/app.py (which would be circular). Every kitchen
blueprint refuses anonymous requests in ``before_request``; only the public
blueprint (calendar feed, share links) is reachable without a session, and it
authenticates by token instead.
"""

from __future__ import annotations

from typing import Any, Callable

from flask import Blueprint, Flask, jsonify, request

from helpers import setup_logger
from kitchen import db
from kitchen.recipes import RecipeInvalid, RecipeNotFound

logger = setup_logger(__name__)

_current_user: Callable[[], str | None] = lambda: None
_is_admin: Callable[[], bool] = lambda: False
_socketio: Any = None

MAX_JSON_BYTES = 2 * 1024 * 1024


class KitchenError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def current_user() -> str:
    user = _current_user()
    if not user:
        raise KitchenError("Authentication required", 401)
    return user


def is_admin() -> bool:
    return bool(_is_admin())


def body() -> dict:
    if request.content_length and request.content_length > MAX_JSON_BYTES:
        raise KitchenError("Request body too large", 413)
    data = request.get_json(silent=True)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise KitchenError("JSON object required")
    return data


def arg_list(name: str) -> list[str]:
    values: list[str] = []
    for raw in request.args.getlist(name):
        values.extend(v.strip() for v in raw.split(",") if v.strip())
    return values[:20]


def arg_int(name: str, default: int | None = None, *, lo: int = 0, hi: int = 10_000) -> int | None:
    raw = request.args.get(name)
    if raw in (None, ""):
        return default
    try:
        return max(lo, min(hi, int(raw)))
    except ValueError:
        raise KitchenError(f"{name} must be a number")


def arg_float(name: str) -> float | None:
    raw = request.args.get(name)
    if raw in (None, ""):
        return None
    try:
        value = float(raw)
    except ValueError:
        raise KitchenError(f"{name} must be a number")
    if not 0 < value <= 500:
        raise KitchenError(f"{name} is out of range")
    return value


def notify(topic: str, **payload: Any) -> None:
    """Tell every signed-in client that shared kitchen data changed."""
    if _socketio is None:
        return
    try:
        _socketio.emit("kitchen_changed", {"topic": topic, **payload})
    except Exception as exc:
        logger.debug("[Kitchen] change broadcast failed: %s", exc)


def protected_blueprint(name: str, import_name: str) -> Blueprint:
    bp = Blueprint(name, import_name)

    @bp.before_request
    def _require_login():
        if not _current_user():
            return jsonify({"error": "Authentication required"}), 401
        db.migrate()
        return None

    _register_errors(bp)
    return bp


def public_blueprint(name: str, import_name: str) -> Blueprint:
    bp = Blueprint(name, import_name)

    @bp.before_request
    def _ready():
        db.migrate()

    _register_errors(bp)
    return bp


def _register_errors(bp: Blueprint) -> None:
    @bp.errorhandler(KitchenError)
    def _kitchen_error(exc: KitchenError):
        return jsonify({"error": str(exc)}), exc.status

    @bp.errorhandler(RecipeNotFound)
    def _not_found(_exc):
        return jsonify({"error": "Not found"}), 404

    @bp.errorhandler(RecipeInvalid)
    def _invalid(exc):
        return jsonify({"error": str(exc)}), 400


def init_app(app: Flask, *, socketio: Any, current_user: Callable[[], str | None],
             is_admin: Callable[[], bool], start_backfill: bool = True) -> None:
    global _current_user, _is_admin, _socketio
    _current_user = current_user
    _is_admin = is_admin
    _socketio = socketio

    db.migrate()

    from kitchen import (
        import_api, mealplan_api, pantry_api, public_api, recipes_api, shopping_api, souschef_api,
    )

    for module in (recipes_api, mealplan_api, shopping_api, pantry_api, souschef_api, import_api):
        app.register_blueprint(module.bp, url_prefix="/api/kitchen")
    app.register_blueprint(public_api.bp)

    if start_backfill:
        from kitchen.library import start_backfill_thread
        start_backfill_thread()
