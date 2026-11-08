"""Bring recipes in from other apps and files.

Each source is turned into a Schema.org-shaped dict that ``save_from_schema``
already understands, so the library has one write path. Mealie and Tandoor
are read with the host and API key from the settings store (never from
code); Paprika and Schema.org JSON come in as uploads.
"""

from __future__ import annotations

import base64
import gzip
import io
import json
import re
import zipfile
from typing import Any, Iterator
from urllib.parse import urljoin, urlparse

from helpers import setup_logger
from kitchen.timers import detect_timers

logger = setup_logger(__name__)

MAX_ARCHIVE_BYTES = 200 * 1024 * 1024
MAX_ENTRY_BYTES = 20 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 5000
MAX_REMOTE_RECIPES = 5000
PAGE_SIZE = 50


class ImportFailed(RuntimeError):
    pass


# ===== Small helpers =====

def _text(value: Any, limit: int = 4000) -> str:
    return " ".join(str(value or "").split())[:limit]


def _number(value: Any) -> str:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return ""
    if num <= 0:
        return ""
    return str(int(num)) if num == int(num) else f"{num:g}"


def text_duration(value: Any) -> str:
    """ISO 8601 duration from "PT20M", 20, "1 hour 15 min", "20 דקות"... or ""."""
    if value in (None, ""):
        return ""
    if isinstance(value, (int, float)):
        minutes = int(value)
    else:
        text = str(value).strip()
        if re.fullmatch(r"P(T(\d+H)?(\d+M)?(\d+S)?|\d+D)", text, re.I):
            return text.upper()
        if text.isdigit():
            minutes = int(text)
        else:
            minutes = round(sum(t["seconds"] for t in detect_timers(text)) / 60)
    if minutes <= 0:
        return ""
    hours, mins = divmod(minutes, 60)
    return "PT" + (f"{hours}H" if hours else "") + (f"{mins}M" if mins else "")


def _lines(value: Any) -> list[str]:
    return [line.strip() for line in str(value or "").splitlines() if line.strip()]


def _names(items: Any) -> list[str]:
    out = []
    for item in items or []:
        name = item.get("name") if isinstance(item, dict) else item
        name = _text(name, 60)
        if name:
            out.append(name)
    return out


# ===== Schema.org JSON =====

def _is_recipe(node: Any) -> bool:
    if not isinstance(node, dict):
        return False
    kind = node.get("@type")
    kinds = kind if isinstance(kind, list) else [kind]
    return any(str(k).lower() == "recipe" for k in kinds)


def find_schema_recipes(data: Any, _depth: int = 0) -> list[dict]:
    """Every Schema.org Recipe in a JSON document (list, @graph, or nested)."""
    if _depth > 6:
        return []
    if _is_recipe(data):
        return [data]
    found: list[dict] = []
    if isinstance(data, list):
        for item in data[:2000]:
            found.extend(find_schema_recipes(item, _depth + 1))
    elif isinstance(data, dict):
        for key in ("@graph", "recipes", "itemListElement", "item"):
            if key in data:
                found.extend(find_schema_recipes(data[key], _depth + 1))
    return found


def schema_image_url(schema: dict, base: str = "") -> str:
    image = schema.get("image")
    if isinstance(image, list):
        image = image[0] if image else None
    if isinstance(image, dict):
        image = image.get("url") or image.get("contentUrl")
    if not isinstance(image, str) or not image.strip():
        return ""
    url = urljoin(base, image.strip()) if base else image.strip()
    return url if urlparse(url).scheme in ("http", "https") else ""


def clean_schema(schema: dict) -> dict:
    """A recipe document we are willing to store: no images, no executable junk."""
    keep = (
        "name", "description", "recipeYield", "prepTime", "cookTime", "totalTime", "recipeIngredient",
        "recipeIngredients", "recipeInstructions", "keywords", "recipeCategory", "recipeCuisine", "tags",
        "nutrition", "url", "inLanguage", "servings",
    )
    out = {k: schema[k] for k in keep if k in schema}
    for key in ("prepTime", "cookTime", "totalTime"):
        if key in out:
            out[key] = text_duration(out[key])
    out["@context"] = "https://schema.org"
    out["@type"] = "Recipe"
    return out


# ===== Mealie =====

def _mealie_ingredient(ing: Any) -> dict | str | None:
    if isinstance(ing, str):
        return ing
    if not isinstance(ing, dict):
        return None
    raw = _text(ing.get("originalText") or ing.get("display") or "", 400)
    food = _text((ing.get("food") or {}).get("name") if isinstance(ing.get("food"), dict) else "", 200)
    unit = _text((ing.get("unit") or {}).get("name") if isinstance(ing.get("unit"), dict) else "", 40)
    note = _text(ing.get("note"), 400)
    if ing.get("disableAmount") or not food:
        line = raw or note
        return line or None
    qty = _number(ing.get("quantity"))
    if not raw:
        raw = " ".join(p for p in (qty, unit, food, note) if p)
    return {"food": food, "quantity": qty, "unit": unit, "notes": note, "raw": raw}


def mealie_to_schema(detail: dict, host: str = "") -> dict:
    ingredients = [x for x in (_mealie_ingredient(i) for i in detail.get("recipeIngredient") or []) if x]
    structured = [i for i in ingredients if isinstance(i, dict)]
    lines = [i if isinstance(i, str) else i["raw"] for i in ingredients]
    steps = []
    for s in detail.get("recipeInstructions") or []:
        text = s.get("text") if isinstance(s, dict) else s
        if _text(text):
            steps.append({"@type": "HowToStep", "text": str(text).strip()})
    servings = _number(detail.get("recipeServings"))
    schema = {
        "name": _text(detail.get("name"), 200),
        "description": _text(detail.get("description")),
        "recipeYield": _text(detail.get("recipeYield"), 200) or (f"{servings} servings" if servings else ""),
        "prepTime": text_duration(detail.get("prepTime")),
        "cookTime": text_duration(detail.get("performTime") or detail.get("cookTime")),
        "totalTime": text_duration(detail.get("totalTime")),
        "recipeIngredient": lines,
        "recipeInstructions": steps,
        "keywords": _names(detail.get("tags")),
        "recipeCategory": _names(detail.get("recipeCategory")),
        "url": _text(detail.get("orgURL"), 2000),
    }
    # Only use the structured list when every line parsed; a partial one would drop lines.
    if structured and len(structured) == len(ingredients):
        schema["recipeIngredients"] = structured
    nutrition = detail.get("nutrition")
    if isinstance(nutrition, dict) and any(nutrition.values()):
        schema["nutrition"] = {k: str(v) for k, v in nutrition.items() if v not in (None, "")}
    return clean_schema(schema)


def _client(kind: str):
    from config import config

    if kind == "mealie":
        from mealie import Mealie
        if not config.MEALIE_HOST or not config.MEALIE_API_KEY:
            raise ImportFailed("Mealie host and API key are not set in Settings")
        return Mealie()
    from tandoor import Tandoor
    if not config.TANDOOR_HOST or not config.TANDOOR_API_KEY:
        raise ImportFailed("Tandoor host and API key are not set in Settings")
    return Tandoor()


def _get_json(client, url: str) -> Any:
    try:
        resp = client._session.get(url, headers=client._build_headers(), timeout=30)
    except Exception as exc:
        raise ImportFailed(f"Could not reach {client._name}: {type(exc).__name__}") from exc
    if resp.status_code in (401, 403):
        raise ImportFailed(f"{client._name} rejected the API key")
    if resp.status_code >= 400:
        raise ImportFailed(f"{client._name} answered {resp.status_code}")
    try:
        return resp.json()
    except ValueError as exc:
        raise ImportFailed(f"{client._name} did not return JSON") from exc


def _same_host(url: str, base: str) -> bool:
    a, b = urlparse(url), urlparse(base)
    return bool(a.scheme in ("http", "https") and a.netloc and a.netloc == b.netloc)


def _get_image(client, url: str) -> bytes | None:
    """Only from the configured server: the key must not travel to other hosts."""
    if not url or not _same_host(url, client.base_url):
        return None
    try:
        resp = client._session.get(
            url, headers={"Authorization": f"Bearer {client.api_key}"}, timeout=30, stream=True,
        )
        if resp.status_code != 200:
            return None
        data = b""
        for chunk in resp.iter_content(65536):
            data += chunk
            if len(data) > 15 * 1024 * 1024:
                return None
        return data
    except Exception:
        return None


def list_remote(kind: str) -> list[dict]:
    """[{source_id, name, url}] for every recipe on the remote server."""
    client = _client(kind)
    out: list[dict] = []
    page = 1
    while len(out) < MAX_REMOTE_RECIPES:
        if kind == "mealie":
            data = _get_json(client, f"{client.base_url}/api/recipes?page={page}&perPage={PAGE_SIZE}")
            items = data.get("items") or []
            for it in items:
                out.append({"source_id": it.get("slug") or it.get("id"), "name": _text(it.get("name"), 200),
                            "url": _text(it.get("orgURL"), 2000)})
            total_pages = int(data.get("total_pages") or data.get("totalPages") or page)
            if page >= total_pages or not items:
                break
        else:
            data = _get_json(client, f"{client.base_url}/api/recipe/?page={page}&page_size={PAGE_SIZE}")
            items = data.get("results") or []
            for it in items:
                out.append({"source_id": str(it.get("id")), "name": _text(it.get("name"), 200),
                            "url": _text(it.get("source_url"), 2000)})
            if not data.get("next") or not items:
                break
        page += 1
    return [o for o in out if o["source_id"] and o["name"]]


def fetch_remote(kind: str, source_id: str, client=None) -> tuple[dict, bytes | None]:
    client = client or _client(kind)
    safe_id = re.sub(r"[^\w-]", "", str(source_id))[:200]
    if not safe_id:
        raise ImportFailed("bad recipe id")
    if kind == "mealie":
        detail = _get_json(client, f"{client.base_url}/api/recipes/{safe_id}")
        image = None
        if detail.get("id") and detail.get("image"):
            image = _get_image(client, f"{client.base_url}/api/media/recipes/{detail['id']}/images/original.webp")
        return mealie_to_schema(detail), image
    detail = _get_json(client, f"{client.base_url}/api/recipe/{safe_id}/")
    image = _get_image(client, urljoin(client.base_url + "/", str(detail.get("image") or "")))
    return tandoor_to_schema(detail), image


def remote_client(kind: str):
    return _client(kind)


# ===== Tandoor =====

def tandoor_to_schema(detail: dict) -> dict:
    lines: list[str] = []
    structured: list[dict] = []
    steps = []
    for step in detail.get("steps") or []:
        for ing in step.get("ingredients") or []:
            if not isinstance(ing, dict) or ing.get("is_header"):
                continue
            food = _text((ing.get("food") or {}).get("name") if isinstance(ing.get("food"), dict) else "", 200)
            unit = _text((ing.get("unit") or {}).get("name") if isinstance(ing.get("unit"), dict) else "", 40)
            note = _text(ing.get("note"), 400)
            qty = "" if ing.get("no_amount") else _number(ing.get("amount"))
            raw = _text(ing.get("original_text"), 400) or " ".join(p for p in (qty, unit, food, note) if p)
            if not raw:
                continue
            lines.append(raw)
            if food:
                structured.append({"food": food, "quantity": qty, "unit": unit, "notes": note, "raw": raw})
        text = _text(step.get("instruction") or step.get("instructions_markdown"), 4000)
        if text:
            name = _text(step.get("name"), 120)
            steps.append({"@type": "HowToStep", "text": f"{name}: {text}" if name and name not in text else text})
    prep = int(detail.get("working_time") or 0)
    wait = int(detail.get("waiting_time") or 0)
    servings = _number(detail.get("servings"))
    schema = {
        "name": _text(detail.get("name"), 200),
        "description": _text(detail.get("description")),
        "recipeYield": f"{servings} {_text(detail.get('servings_text'), 40) or 'servings'}" if servings else "",
        "prepTime": text_duration(prep),
        "cookTime": text_duration(wait),
        "totalTime": text_duration(prep + wait),
        "recipeIngredient": lines,
        "recipeInstructions": steps,
        "keywords": _names(detail.get("keywords")),
        "url": _text(detail.get("source_url"), 2000),
    }
    if structured and len(structured) == len(lines):
        schema["recipeIngredients"] = structured
    n = detail.get("nutrition")
    if isinstance(n, dict):
        mapped = {
            "calories": n.get("calories"), "proteinContent": n.get("proteins"),
            "fatContent": n.get("fats"), "carbohydrateContent": n.get("carbohydrates"),
        }
        mapped = {k: str(v) for k, v in mapped.items() if v not in (None, "", 0)}
        if mapped:
            schema["nutrition"] = mapped
    return clean_schema(schema)


# ===== Paprika =====

def paprika_to_schema(entry: dict) -> tuple[dict, bytes | None]:
    steps = [{"@type": "HowToStep", "text": line} for line in _lines(entry.get("directions"))]
    notes = _text(entry.get("notes"), 2000)
    description = _text(entry.get("description"))
    if notes:
        description = f"{description}\n\n{notes}".strip() if description else notes
    schema = clean_schema({
        "name": _text(entry.get("name"), 200),
        "description": description,
        "recipeYield": _text(entry.get("servings"), 200),
        "prepTime": entry.get("prep_time"),
        "cookTime": entry.get("cook_time"),
        "totalTime": entry.get("total_time"),
        "recipeIngredient": _lines(entry.get("ingredients")),
        "recipeInstructions": steps,
        "recipeCategory": _names(entry.get("categories")),
        "url": _text(entry.get("source_url"), 2000),
    })
    photo = None
    if entry.get("photo_data"):
        try:
            photo = base64.b64decode(entry["photo_data"], validate=False)
        except (ValueError, TypeError):
            photo = None
    return schema, photo


def _read_capped(handle, limit: int) -> bytes:
    data = handle.read(limit + 1)
    if len(data) > limit:
        raise ImportFailed("A recipe in the archive is too large")
    return data


def read_paprika(data: bytes) -> Iterator[tuple[dict, bytes | None]]:
    """Recipes from a .paprikarecipes export (a zip of gzipped JSON files).

    Nothing is extracted to disk, and both the compressed and the inflated
    sizes are capped, so a zip bomb runs out of budget instead of memory.
    """
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ImportFailed("Archive is larger than 200 MB")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        # A single exported recipe is just the gzip file.
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(data)) as gz:
                entry = json.loads(_read_capped(gz, MAX_ENTRY_BYTES))
            yield paprika_to_schema(entry)
            return
        except (OSError, ValueError):
            raise ImportFailed("Not a Paprika export") from exc
    infos = [i for i in archive.infolist() if not i.is_dir()][:MAX_ARCHIVE_ENTRIES]
    for info in infos:
        if info.file_size > MAX_ENTRY_BYTES * 4:
            continue
        try:
            with archive.open(info) as raw, gzip.GzipFile(fileobj=io.BytesIO(_read_capped(raw, MAX_ENTRY_BYTES))) as gz:
                entry = json.loads(_read_capped(gz, MAX_ENTRY_BYTES))
        except (OSError, ValueError, ImportFailed) as exc:
            logger.info("[Import] skipped %s: %s", info.filename[:80], exc)
            continue
        if isinstance(entry, dict) and entry.get("name"):
            yield paprika_to_schema(entry)


# ===== Photos / PDF =====

_VISION_PROMPT = """These images show one recipe (a cookbook page, a card, a screenshot).
Transcribe it into the recipe JSON schema. Keep the original wording and quantities; do not
invent anything that is not visible. If the images do not show a recipe, set is_recipe=false
and explain in rejection_reason. Every ingredient needs food, quantity, unit ("" if none),
notes and raw (the line as written). datePublished may be "". Keep the recipe's own language.
"""

_TEXT_PROMPT = """The text below was extracted from a document that contains one recipe.
Transcribe it into the recipe JSON schema. Keep the original wording and quantities; do not
invent anything. If it is not a recipe, set is_recipe=false and explain in rejection_reason.
Every ingredient needs food, quantity, unit ("" if none), notes and raw. datePublished may be "".
Keep the recipe's own language.
"""


def _extraction_to_schema(text: str) -> dict:
    from recipe_schema import parse_recipe_extraction, recipe_dict_from_extraction

    extraction = parse_recipe_extraction(text)
    if not extraction.is_recipe:
        raise ImportFailed(extraction.rejection_reason or "That does not look like a recipe")
    schema = recipe_dict_from_extraction(extraction)
    schema.pop("datePublished", None)
    return clean_schema(schema)


def _session():
    from ai_providers import AiSession

    try:
        return AiSession()
    except Exception as exc:
        raise ImportFailed(f"No AI provider is configured: {exc}") from exc


def from_photos(paths: list[str]) -> dict:
    from recipe_schema import RecipeExtraction, to_openai_json_schema

    try:
        text = _session().complete_json_with_images(
            _VISION_PROMPT, paths, schema_name="recipe_extraction",
            json_schema=to_openai_json_schema(RecipeExtraction), schema_model=RecipeExtraction, detail="high",
        )
    except ImportFailed:
        raise
    except Exception as exc:
        raise ImportFailed(f"The AI provider could not read the photos: {type(exc).__name__}") from exc
    return _extraction_to_schema(text)


def pdf_text(data: bytes, max_pages: int = 20) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise ImportFailed("PDF import needs the 'pypdf' package") from exc
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [page.extract_text() or "" for page in reader.pages[:max_pages]]
    except Exception as exc:
        raise ImportFailed("Could not read that PDF") from exc
    return "\n".join(pages).strip()[:60000]


def from_pdf(data: bytes) -> dict:
    from recipe_schema import RecipeExtraction, to_openai_json_schema

    text = pdf_text(data)
    if len(text) < 40:
        raise ImportFailed("This PDF has no text layer (it is probably a scan). Import it as photos instead.")
    try:
        answer = _session().complete_json(
            _TEXT_PROMPT, text, schema_name="recipe_extraction",
            json_schema=to_openai_json_schema(RecipeExtraction), schema_model=RecipeExtraction,
        )
    except ImportFailed:
        raise
    except Exception as exc:
        raise ImportFailed(f"The AI provider could not read the PDF: {type(exc).__name__}") from exc
    return _extraction_to_schema(answer)
