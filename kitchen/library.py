"""Bridges from the extraction pipeline and old history into the library."""

from __future__ import annotations

import base64
import json
import threading

from helpers import setup_logger
from kitchen import db, recipes

logger = setup_logger(__name__)

_BACKFILL_KEY = "history_backfill_done"


def _source_kind(recipe_data: dict) -> str:
    video = recipe_data.get("video")
    url = str(recipe_data.get("url") or "")
    if isinstance(video, dict) and video.get("url") and any(
        host in url for host in ("tiktok", "youtu", "instagram", "facebook", "vimeo")
    ):
        return "video"
    return "web" if url else "manual"


def save_pipeline_recipe(recipe_data: dict, image_path: str | None,
                         created_by: str | None = None) -> str:
    """Save an approved extraction. Re-extracting a URL updates that recipe."""
    db.migrate()
    existing = recipes.find_existing(source_url=str(recipe_data.get("url") or ""))
    return recipes.save_from_schema(
        recipe_data,
        image_path=image_path,
        created_by=created_by,
        source_kind=_source_kind(recipe_data),
        replace_recipe_id=existing,
    )


def backfill_from_history() -> int:
    """Copy successful history rows into the library, once per database."""
    db.migrate()
    if db.get_meta(_BACKFILL_KEY):
        return 0
    added = 0
    try:
        with db.connect() as conn:
            try:
                rows = conn.execute(
                    "SELECT id, url, recipe_data, thumbnail_data FROM recipe_history"
                    " WHERE status = 'success' AND recipe_data IS NOT NULL ORDER BY id"
                ).fetchall()
            except Exception:
                rows = []
        for row in rows:
            try:
                data = json.loads(row["recipe_data"])
            except (TypeError, ValueError):
                continue
            if not isinstance(data, dict):
                continue
            data.setdefault("url", row["url"] or "")
            if recipes.find_existing(source_url=str(data.get("url") or ""), name=str(data.get("name") or "")):
                continue
            image_bytes = None
            thumb = row["thumbnail_data"] or ""
            if thumb:
                try:
                    image_bytes = base64.b64decode(thumb.split(",", 1)[-1], validate=False)
                except (ValueError, TypeError):
                    image_bytes = None
            try:
                recipes.save_from_schema(
                    data, image_bytes=image_bytes, source_kind=_source_kind(data),
                    history_id=row["id"],
                )
                added += 1
            except (recipes.RecipeInvalid, ValueError) as exc:
                logger.info("[Kitchen] history %s not imported: %s", row["id"], exc)
    finally:
        db.set_meta(_BACKFILL_KEY, "1")
    if added:
        logger.info("[Kitchen] copied %d recipe(s) from history into the library", added)
    return added


def start_backfill_thread() -> threading.Thread:
    """Run the backfill without holding up the server start."""
    def run() -> None:
        try:
            backfill_from_history()
        except Exception as exc:
            logger.warning("[Kitchen] history backfill failed: %s", exc)

    thread = threading.Thread(target=run, name="kitchen-backfill", daemon=True)
    thread.start()
    return thread
