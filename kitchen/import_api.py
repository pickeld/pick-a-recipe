"""Import endpoints: /api/kitchen/import/*.

Every import is two steps: read the source into a staged batch the user can
review (with duplicates flagged), then commit the chosen recipes. Bulk reads
from Mealie or Tandoor use the server's stored credentials, so only admins
may run them, and they run as a background job.
"""

from __future__ import annotations

import os
import secrets
import tempfile
import threading
import time
import uuid
from typing import Any

from flask import jsonify, request

from helpers import setup_logger
from kitchen import images, importers
from kitchen.recipes import RecipeInvalid, find_existing, save_from_schema
from kitchen.web import KitchenError, body, current_user, is_admin, notify, protected_blueprint

logger = setup_logger(__name__)

bp = protected_blueprint("kitchen_import", __name__)

STAGE_TTL = 3600
MAX_STAGES = 20
MAX_PHOTOS = 8
MAX_UPLOAD = importers.MAX_ARCHIVE_BYTES

_lock = threading.Lock()
_stages: dict[str, dict] = {}
_jobs: dict[str, dict] = {}


def _prune() -> None:
    now = time.time()
    for key in [k for k, v in _stages.items() if now - v["created"] > STAGE_TTL]:
        _stages.pop(key, None)
    for key in [k for k, v in _jobs.items() if v.get("finished") and now - v["finished"] > STAGE_TTL]:
        _jobs.pop(key, None)
    while len(_stages) > MAX_STAGES:
        oldest = min(_stages, key=lambda k: _stages[k]["created"])
        _stages.pop(oldest, None)


def _preview(index: int, schema: dict) -> dict:
    duplicate = find_existing(str(schema.get("url") or ""), str(schema.get("name") or ""))
    return {
        "index": index,
        "name": schema.get("name") or "",
        "ingredients": len(schema.get("recipeIngredient") or schema.get("recipeIngredients") or []),
        "steps": len(schema.get("recipeInstructions") or []),
        "url": schema.get("url") or "",
        "duplicate_of": duplicate,
    }


def _stage(kind: str, recipes: list[tuple[dict, bytes | None]]) -> dict:
    recipes = [(s, img) for s, img in recipes if str(s.get("name") or "").strip()]
    if not recipes:
        raise KitchenError("No recipes found in that source", 422)
    token = secrets.token_urlsafe(16)
    with _lock:
        _prune()
        _stages[token] = {"user": current_user(), "created": time.time(), "kind": kind, "recipes": recipes}
    previews = [_preview(i, s) for i, (s, _img) in enumerate(recipes)]
    out: dict[str, Any] = {"token": token, "kind": kind, "count": len(recipes), "recipes": previews}
    if len(recipes) == 1:
        out["draft"] = recipes[0][0]
        out["has_image"] = recipes[0][1] is not None
    return out


def _source_kind(kind: str) -> str:
    return "web" if kind == "url" else "import"


@bp.get("/import/sources")
def sources():
    from config import config
    return jsonify({
        "mealie": bool(config.MEALIE_HOST and config.MEALIE_API_KEY),
        "tandoor": bool(config.TANDOOR_HOST and config.TANDOOR_API_KEY),
        "admin": is_admin(),
    })


@bp.post("/import/file")
def import_file():
    if request.content_length and request.content_length > MAX_UPLOAD:
        raise KitchenError("Upload is too large", 413)
    upload = request.files.get("file")
    if upload is None:
        raise KitchenError("file required")
    name = (upload.filename or "").lower()
    data = upload.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise KitchenError("Upload is too large", 413)
    try:
        if name.endswith((".paprikarecipes", ".paprikarecipe")):
            return jsonify(_stage("paprika", list(importers.read_paprika(data))))
        if name.endswith(".pdf") or data.startswith(b"%PDF"):
            return jsonify(_stage("pdf", [(importers.from_pdf(data), None)]))
        if name.endswith(".json") or data.lstrip()[:1] in (b"{", b"["):
            import json
            try:
                doc = json.loads(data.decode("utf-8-sig"))
            except (UnicodeDecodeError, ValueError):
                raise KitchenError("That file is not valid JSON")
            found = importers.find_schema_recipes(doc)
            return jsonify(_stage("json", [(importers.clean_schema(r), None) for r in found]))
    except importers.ImportFailed as exc:
        raise KitchenError(str(exc), 422)
    raise KitchenError("Unsupported file. Use a Paprika export, Schema.org JSON, a PDF, or photos.")


@bp.post("/import/schema")
def import_schema():
    data = body()
    doc = data.get("json")
    if isinstance(doc, str):
        import json
        try:
            doc = json.loads(doc)
        except ValueError:
            raise KitchenError("That is not valid JSON")
    found = importers.find_schema_recipes(doc)
    return jsonify(_stage("json", [(importers.clean_schema(r), None) for r in found]))


@bp.post("/import/photos")
def import_photos():
    files = request.files.getlist("photos")[:MAX_PHOTOS]
    if not files:
        raise KitchenError("photos required")
    cover: bytes | None = None
    with tempfile.TemporaryDirectory(prefix="kitchen-photos-") as tmp:
        paths = []
        for i, f in enumerate(files):
            raw = f.read(images.MAX_IMAGE_BYTES + 1)
            if len(raw) > images.MAX_IMAGE_BYTES or images.sniff(raw) is None:
                raise KitchenError(f"Photo {i + 1} is not a supported image under 15 MB")
            try:
                jpeg = images._reencode(raw)
            except images.ImageError as exc:
                raise KitchenError(str(exc))
            path = os.path.join(tmp, f"page{i}.jpg")
            with open(path, "wb") as fh:
                fh.write(jpeg)
            paths.append(path)
            if cover is None:
                cover = jpeg
        try:
            schema = importers.from_photos(paths)
        except importers.ImportFailed as exc:
            raise KitchenError(str(exc), 422)
    # A photographed page makes a poor cover; keep it only if the user wants it.
    staged = _stage("photo", [(schema, cover)])
    return jsonify(staged)


@bp.post("/import/url")
def import_url():
    from url_safety import assert_public_http_url
    from web_recipe_fetcher import download_image, fetch_web_recipe, is_video_url

    url = str(body().get("url") or "").strip()
    if not url:
        raise KitchenError("url required")
    try:
        assert_public_http_url(url)
    except Exception:
        raise KitchenError("That URL is not a public http(s) address")
    existing = find_existing(url)
    if is_video_url(url):
        return jsonify({"pipeline": True, "duplicate_of": existing})
    try:
        page = fetch_web_recipe(url)
    except Exception as exc:
        logger.info("[Import] fetch failed for %s: %s", url[:200], exc)
        return jsonify({"pipeline": True, "duplicate_of": existing})
    structured = page.get("structured")
    if not structured or not structured.get("recipeIngredient"):
        # No usable Schema.org data: let the regular AI pipeline read the page.
        return jsonify({"pipeline": True, "duplicate_of": existing})
    schema = importers.clean_schema({**structured, "url": url})
    image = None
    image_url = page.get("image_url") or importers.schema_image_url(structured, url)
    if image_url:
        with tempfile.TemporaryDirectory(prefix="kitchen-url-") as tmp:
            path = download_image(image_url, tmp)
            if path and os.path.getsize(path) <= images.MAX_IMAGE_BYTES:
                with open(path, "rb") as fh:
                    image = fh.read()
    return jsonify(_stage("url", [(schema, image)]))


@bp.post("/import/commit")
def commit():
    data = body()
    token = str(data.get("token") or "")
    with _lock:
        _prune()
        stage = _stages.get(token)
    if not stage or stage["user"] != current_user():
        raise KitchenError("This import expired. Start it again.", 410)
    recipes = stage["recipes"]
    indexes = data.get("indexes")
    if indexes is None:
        indexes = list(range(len(recipes)))
    if not isinstance(indexes, list):
        raise KitchenError("indexes must be a list")
    on_duplicate = data.get("on_duplicate") or "skip"
    if on_duplicate not in ("skip", "replace", "keep_both"):
        raise KitchenError("on_duplicate must be skip, replace or keep_both")
    overrides = data.get("overrides") if isinstance(data.get("overrides"), dict) else {}
    use_image = data.get("use_image", True) is not False
    ids, skipped, failed = [], 0, []
    for idx in indexes:
        if not isinstance(idx, int) or not 0 <= idx < len(recipes):
            continue
        schema, image = recipes[idx]
        override = overrides.get(str(idx))
        if isinstance(override, dict):
            schema = importers.clean_schema({**schema, **override})
        duplicate = find_existing(str(schema.get("url") or ""), str(schema.get("name") or ""))
        if duplicate and on_duplicate == "skip":
            skipped += 1
            continue
        try:
            ids.append(save_from_schema(
                schema, image_bytes=image if use_image else None, created_by=current_user(),
                source_kind=_source_kind(stage["kind"]),
                replace_recipe_id=duplicate if duplicate and on_duplicate == "replace" else None,
            ))
        except RecipeInvalid as exc:
            failed.append({"index": idx, "name": schema.get("name"), "error": str(exc)})
    with _lock:
        _stages.pop(token, None)
    if ids:
        notify("recipes")
    return jsonify({"imported": len(ids), "ids": ids, "skipped": skipped, "failed": failed})


# ===== Mealie / Tandoor =====

def _check_kind(kind: str) -> None:
    if kind not in ("mealie", "tandoor"):
        raise KitchenError("Unknown source", 404)
    if not is_admin():
        raise KitchenError("Only admins can import from Mealie or Tandoor", 403)


@bp.get("/import/remote/<kind>")
def remote_list(kind: str):
    _check_kind(kind)
    try:
        items = importers.list_remote(kind)
    except importers.ImportFailed as exc:
        raise KitchenError(str(exc), 502)
    for item in items:
        item["duplicate_of"] = find_existing(item["url"], item["name"])
    return jsonify({"recipes": items})


def _run_remote(job_id: str, kind: str, ids: list[str], on_duplicate: str, user: str) -> None:
    job = _jobs[job_id]
    try:
        client = importers.remote_client(kind)
        for source_id in ids:
            if job.get("cancel"):
                break
            try:
                schema, image = importers.fetch_remote(kind, source_id, client)
                duplicate = find_existing(str(schema.get("url") or ""), str(schema.get("name") or ""))
                if duplicate and on_duplicate == "skip":
                    job["skipped"] += 1
                else:
                    save_from_schema(
                        schema, image_bytes=image, created_by=user, source_kind="import",
                        replace_recipe_id=duplicate if duplicate and on_duplicate == "replace" else None,
                    )
                    job["imported"] += 1
            except (importers.ImportFailed, RecipeInvalid) as exc:
                job["failed"].append({"id": source_id, "error": str(exc)})
            except Exception as exc:
                logger.warning("[Import] %s %s failed: %s", kind, source_id, exc)
                job["failed"].append({"id": source_id, "error": type(exc).__name__})
            job["done"] += 1
            if job["done"] % 10 == 0:
                notify("import", job=job_id)
    except importers.ImportFailed as exc:
        job["error"] = str(exc)
    finally:
        job["status"] = "cancelled" if job.get("cancel") else ("failed" if job.get("error") else "done")
        job["finished"] = time.time()
        notify("import", job=job_id)
        notify("recipes")


@bp.post("/import/remote/<kind>")
def remote_import(kind: str):
    _check_kind(kind)
    data = body()
    on_duplicate = data.get("on_duplicate") or "skip"
    if on_duplicate not in ("skip", "replace", "keep_both"):
        raise KitchenError("on_duplicate must be skip, replace or keep_both")
    ids = data.get("ids")
    if ids == "all" or data.get("all"):
        try:
            ids = [i["source_id"] for i in importers.list_remote(kind)]
        except importers.ImportFailed as exc:
            raise KitchenError(str(exc), 502)
    if not isinstance(ids, list) or not ids:
        raise KitchenError("ids required")
    ids = [str(i) for i in ids[:importers.MAX_REMOTE_RECIPES]]
    job_id = uuid.uuid4().hex
    with _lock:
        _prune()
        if any(j["status"] == "running" for j in _jobs.values()):
            raise KitchenError("Another import is already running", 409)
        _jobs[job_id] = {
            "id": job_id, "kind": kind, "status": "running", "total": len(ids), "done": 0,
            "imported": 0, "skipped": 0, "failed": [], "user": current_user(), "finished": None,
        }
    threading.Thread(
        target=_run_remote, args=(job_id, kind, ids, on_duplicate, current_user()),
        name=f"kitchen-import-{kind}", daemon=True,
    ).start()
    return jsonify({"job_id": job_id}), 202


@bp.get("/import/jobs/<job_id>")
def job_status(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise KitchenError("Not found", 404)
    return jsonify({k: v for k, v in job.items() if k not in ("cancel",)} | {"failed": job["failed"][:50]})


@bp.delete("/import/jobs/<job_id>")
def cancel_job(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise KitchenError("Not found", 404)
    if not is_admin():
        raise KitchenError("Only admins can cancel imports", 403)
    job["cancel"] = True
    return jsonify({"status": "cancelling"})
