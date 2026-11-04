"""Sous Chef: recipe ideas grounded in the user's own library.

Matching happens here, in code: every recipe is scored by how much of it the
user can make with what they have. The model only sees the top candidates and
is asked to choose and explain. Anything it returns that is not one of those
candidates is dropped, so it cannot point at a recipe that does not exist.
"""

from __future__ import annotations

import json
import random
import re
from datetime import date, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from helpers import setup_logger
from kitchen.aisles import _KEYWORDS, food_key
from kitchen.db import connect
from kitchen.recipes import RecipeInvalid, RecipeNotFound, get_recipe, image_url

logger = setup_logger(__name__)

CANDIDATES = 20
MAX_PICKS = 5

# Too common to count as "having" or "missing" anything.
BASICS = frozenset({
    "salt", "pepper", "black pepper", "water", "oil", "olive oil", "vegetable oil", "canola oil",
    "ice", "מלח", "פלפל", "פלפל שחור", "מים", "שמן", "שמן זית", "שמן קנולה", "קרח",
})

_GLUTEN = ("flour", "bread", "pasta", "noodle", "couscous", "bulgur", "semolina", "breadcrumb", "pita",
           "קמח", "לחם", "פסטה", "אטריות", "קוסקוס", "בורגול", "סולת", "פירורי לחם", "פיתה")
_ANIMAL_EXTRA = ("honey", "gelatin", "דבש", "ג'לטין")

_FISH_EXTRA = ("דגים", "fillet of")

DIETS: dict[str, tuple[str, ...]] = {
    "vegetarian": _KEYWORDS["meat"] + _KEYWORDS["fish"] + _FISH_EXTRA,
    "pescatarian": _KEYWORDS["meat"],
    "vegan": _KEYWORDS["meat"] + _KEYWORDS["fish"] + _FISH_EXTRA + _KEYWORDS["dairy"] + _ANIMAL_EXTRA,
    "gluten-free": _GLUTEN,
    "dairy-free": _KEYWORDS["dairy"],
}


class SousChefUnavailable(RuntimeError):
    """No AI provider is configured, or it failed."""


# ===== Structured outputs =====

class Substitution(BaseModel):
    model_config = ConfigDict(extra="forbid")
    missing: str
    use: str


class SousChefPick(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recipe_id: str
    why: str
    substitutions: list[Substitution]


class SousChefSuggestions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reply: str
    picks: list[SousChefPick]


class SubstituteOption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    use: str
    amount: str
    notes: str


class SubstituteAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    options: list[SubstituteOption]


class PlanAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: str
    slot: str
    recipe_id: str
    why: str


class WeekPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str
    assignments: list[PlanAssignment]


class ReviewIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["ingredient_not_used", "ingredient_missing", "step_missing", "step_unclear", "quantity_missing"]
    detail: str
    suggestion: str


class RecipeReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str
    issues: list[ReviewIssue]


class TagIdeas(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tags: list[str]


class StepLink(BaseModel):
    model_config = ConfigDict(extra="forbid")
    step: int
    ingredients: list[int]


class StepLinks(BaseModel):
    model_config = ConfigDict(extra="forbid")
    steps: list[StepLink]


def _ask(system: str, payload: dict, model: type[BaseModel]) -> BaseModel:
    from ai_providers import AiSession, ProviderConfigError
    from recipe_schema import parse_model, to_openai_json_schema

    try:
        session = AiSession()
    except ProviderConfigError as exc:
        raise SousChefUnavailable(str(exc)) from exc
    except Exception as exc:
        raise SousChefUnavailable(f"No AI provider is configured: {exc}") from exc
    try:
        text = session.complete_json(
            system, json.dumps(payload, ensure_ascii=False),
            schema_name=model.__name__.lower(), json_schema=to_openai_json_schema(model), schema_model=model,
        )
        return parse_model(text, model)
    except SousChefUnavailable:
        raise
    except Exception as exc:
        logger.warning("[Sous Chef] AI call failed: %s", exc)
        raise SousChefUnavailable(f"The AI provider did not answer: {type(exc).__name__}") from exc


def _language() -> str:
    try:
        from config import config
        return config.RECIPE_LANG or "english"
    except Exception:
        return "english"


# ===== Scoring =====

def _contains(a: str, b: str) -> bool:
    return f" {a} " in f" {b} " or f" {b} " in f" {a} "


def has_food(have_keys: list[str], food: str) -> bool:
    key = food_key(food)
    return any(h == key or _contains(h, key) for h in have_keys if h)


def is_basic(food: str) -> bool:
    return food_key(food) in BASICS


# Plant foods whose names contain an animal keyword.
_PLANT_LOOKALIKES = (
    "eggplant", "peanut butter", "almond butter", "nut butter", "coconut milk", "coconut cream",
    "almond milk", "oat milk", "soy milk", "rice milk", "cocoa butter", "butternut", "buttermilk substitute",
    "gluten-free", "gluten free", "חלב קוקוס", "שמנת קוקוס", "חלב שקדים", "חלב סויה", "חלב שיבולת", "חמאת בוטנים",
    "ללא גלוטן",
)


def _keyword_in(word: str, text: str) -> bool:
    if any("\u0590" <= ch <= "\u05FF" for ch in word):
        # Hebrew keywords are stems (e.g. "דג"), so match them only at word starts
        # (allowing the ה/ו/ב/ל prefixes) or "דג" would hit "דגנים".
        return any(
            tok == word or (tok.startswith(word) and len(word) > 2)
            or (tok[:1] in "הובלמש" and tok[1:] == word)
            for tok in text.split()
        )
    return word in text


def _violates_diet(foods: list[str], diets: list[str]) -> bool:
    for diet in diets:
        words = DIETS.get(diet)
        if not words:
            continue
        for food in foods:
            folded = food.casefold()
            for lookalike in _PLANT_LOOKALIKES:
                folded = folded.replace(lookalike, " ")
            if any(_keyword_in(w, folded) for w in words):
                return True
    return False


_MESSAGE_STOPWORDS = frozenset((
    "a", "an", "and", "any", "anything", "are", "can", "dinner", "do", "for", "have", "i", "idea",
    "ideas", "in", "is", "it", "lunch", "me", "meal", "my", "of", "or", "please", "quick", "recipe",
    "recipes", "some", "something", "the", "to", "today", "tonight", "use", "want", "what", "with",
    "without", "you", "breakfast", "easy", "make", "cook", "fast", "up", "items", "that", "expire",
    "soon",
    "או", "אוכל", "אז", "אני", "ארוחה", "ארוחת", "את", "בא", "בוקר", "בלי", "בקלות", "גם", "היום",
    "זה", "יש", "כל", "לבשל", "להכין", "לי", "לנו", "מה", "מהיר", "מהירה", "מתכון", "מתכונים",
    "משהו", "עם", "ערב", "צהריים", "קל", "קלה", "רוצה", "רעיון", "של", "שיש",
))


def message_terms(message: str) -> list[str]:
    """Words worth searching the library for: foods and dish names, not filler."""
    terms: list[str] = []
    for word in re.findall(r"[^\W\d_]+(?:['׳][^\W\d_]+)?", message.casefold()):
        if len(word) < 2 or word in _MESSAGE_STOPWORDS:
            continue
        if any("\u0590" <= ch <= "\u05FF" for ch in word) and len(word) > 3 and word[0] in "הובלמש":
            if word[1:] in _MESSAGE_STOPWORDS:
                continue
            # Keep the bare form too, so "ואספרגוס" finds "אספרגוס".
            terms.append(word[1:])
        terms.append(word)
    return list(dict.fromkeys(terms))[:12]


def _term_in(term: str, text: str) -> bool:
    tokens = text.split()
    if any("\u0590" <= ch <= "\u05FF" for ch in term):
        return _keyword_in(term, text)
    return any(tok == term or (tok.startswith(term) and len(term) > 3) for tok in tokens)


def _library(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT r.id, r.name, r.total_minutes, r.cuisine, r.category,"
        " (SELECT id FROM recipe_images i WHERE i.recipe_id = r.id ORDER BY position LIMIT 1) AS image_id"
        " FROM recipes r"
    ).fetchall()
    recipes = {r["id"]: {**dict(r), "ingredients": [], "tags": []} for r in rows}
    for r in conn.execute("SELECT recipe_id, food, optional FROM recipe_ingredients ORDER BY position"):
        if r["recipe_id"] in recipes:
            recipes[r["recipe_id"]]["ingredients"].append({"food": r["food"], "optional": bool(r["optional"])})
    for r in conn.execute(
        "SELECT rt.recipe_id, t.name FROM recipe_tags rt JOIN tags t ON t.id = rt.tag_id"
    ):
        if r["recipe_id"] in recipes:
            recipes[r["recipe_id"]]["tags"].append(r["name"])
    return list(recipes.values())


def score_recipes(
    have: list[str],
    *,
    username: str | None = None,
    max_minutes: int | None = None,
    diets: list[str] | None = None,
    tags: list[str] | None = None,
    mentions: list[str] | None = None,
    limit: int = CANDIDATES,
    seed: int | None = None,
) -> list[dict]:
    """Rank the library for what the user has. Pure code, no AI.

    ``mentions`` are words from the user's message. A recipe whose name, ingredients or tags
    contain one ranks well above the rest, so the AI gets to see it.
    """
    have_keys = [food_key(h) for h in have if food_key(h)]
    mention_terms = [m.casefold() for m in (mentions or []) if m.strip()]
    today = date.today()
    rng = random.Random(seed)
    with connect() as conn:
        library = _library(conn)
        meta = {}
        last_cooked = {}
        if username:
            meta = {
                r["recipe_id"]: r for r in conn.execute(
                    "SELECT recipe_id, favorite, rating FROM user_recipe_meta WHERE username = ?", (username,)
                )
            }
            last_cooked = {
                r["recipe_id"]: r["last"] for r in conn.execute(
                    "SELECT recipe_id, MAX(cooked_on) AS last FROM cook_log WHERE username = ? GROUP BY recipe_id",
                    (username,),
                )
            }
    wanted_tags = {t.casefold() for t in (tags or [])}
    scored = []
    for recipe in library:
        foods = [i["food"] for i in recipe["ingredients"]]
        if max_minutes and recipe["total_minutes"] and recipe["total_minutes"] > max_minutes:
            continue
        if diets and _violates_diet(foods, diets):
            continue
        if wanted_tags and not wanted_tags & {t.casefold() for t in recipe["tags"]}:
            continue
        relevant = [i["food"] for i in recipe["ingredients"] if not i["optional"] and not is_basic(i["food"])]
        matched = [f for f in relevant if has_food(have_keys, f)] if have_keys else []
        missing = [f for f in relevant if f not in matched] if have_keys else []
        text = " ".join([recipe["name"], *foods, *recipe["tags"]]).casefold()
        mentioned = [m for m in mention_terms if _term_in(m, text)]
        if have_keys and not matched and not mentioned:
            continue
        coverage = (len(matched) / len(relevant)) if relevant else 0.0
        score = coverage * 100 - 6 * len(missing) if have_keys else 0.0
        if mentioned:
            score += 60 + 10 * len(mentioned)
        m = meta.get(recipe["id"])
        if m is not None:
            if m["favorite"]:
                score += 10
            if m["rating"]:
                score += (m["rating"] - 3) * 4
        last = last_cooked.get(recipe["id"])
        if last:
            days = (today - date.fromisoformat(last)).days
            score += -15 if days <= 3 else (-5 if days <= 10 else 5)
        else:
            score += 3
        score += rng.random()
        scored.append({
            "id": recipe["id"],
            "name": recipe["name"],
            "total_minutes": recipe["total_minutes"],
            "tags": recipe["tags"][:8],
            "image_url": image_url(recipe["image_id"]),
            "coverage": round(coverage, 2),
            "have": matched,
            "missing": missing,
            "mentioned": mentioned,
            "score": round(score, 1),
            "favorite": bool(m["favorite"]) if m is not None else False,
            "last_cooked": last,
        })
    scored.sort(key=lambda r: r["score"], reverse=True)
    return scored[:limit]


# ===== Asking =====

_SUGGEST_PROMPT = """You are Sous Chef, a friendly kitchen assistant inside a self-hosted recipe app.
You help the user decide what to cook from THEIR OWN saved recipes.

You receive:
- "candidates": recipes from the user's library, already ranked by code. Each has an id,
  name, minutes, tags, the ingredients the user has ("have") and the ones they lack ("missing").
  "mentioned" lists words from the user's message found in that recipe's name, ingredients or
  tags; a recipe with a non-empty "mentioned" is usually what the user is asking about.
- "on_hand": what the user says they have. "constraints": time, diet, mood or cuisine.
- "history": the conversation so far, and "message": what the user just said.

Rules:
- Only pick recipes from "candidates", using their exact "id". Never invent a recipe or an id.
- Pick at most 5, best first. Prefer high coverage and few missing items, but respect the
  user's message and constraints over the ranking when they conflict.
- "why" is one short sentence per pick. In "substitutions", suggest a swap only for a missing
  ingredient where a common swap exists; leave the list empty otherwise.
- "reply" is a short conversational answer (1-3 sentences). If nothing fits, say so and
  suggest what to buy or that Sous Chef can invent a new recipe.
- Do not assume the user's gender. In Hebrew, address them in the plural ("תרצו", "יש לכם")
  and speak of yourself without gendered verbs where you can ("לא נמצא", not "לא מצאתי").
- Write "reply", "why" and substitutions in {language}.
"""

_UI_LANGUAGES = {"he": "Hebrew", "en": "English"}


def reply_language(lang: str | None) -> str:
    """The language for chat replies: the UI language when known, else the recipe language."""
    return _UI_LANGUAGES.get((lang or "").split("-")[0].lower()) or _language()


def suggest(
    *,
    username: str | None,
    on_hand: list[str],
    message: str = "",
    history: list[dict] | None = None,
    max_minutes: int | None = None,
    diets: list[str] | None = None,
    tags: list[str] | None = None,
    use_ai: bool = True,
    lang: str | None = None,
) -> dict:
    have = [h for h in on_hand if str(h).strip()][:60]
    candidates = score_recipes(
        have, username=username, max_minutes=max_minutes, diets=diets, tags=tags,
        mentions=message_terms(message),
    )
    result: dict[str, Any] = {"candidates": candidates, "picks": [], "reply": "", "ai": False}
    if not candidates:
        return result
    if not use_ai:
        result["picks"] = [{"recipe": c, "why": "", "substitutions": []} for c in candidates[:MAX_PICKS]]
        return result
    payload = {
        "candidates": [
            {k: c[k] for k in ("id", "name", "total_minutes", "tags", "coverage", "have", "missing", "mentioned")}
            for c in candidates
        ],
        "on_hand": have[:60],
        "constraints": {"max_minutes": max_minutes, "diets": diets or [], "tags": tags or []},
        "history": [
            {"role": h.get("role"), "text": str(h.get("text") or "")[:1000]}
            for h in (history or [])[-10:] if isinstance(h, dict) and h.get("role") in ("user", "assistant")
        ],
        "message": message[:1000],
    }
    try:
        answer = _ask(_SUGGEST_PROMPT.format(language=reply_language(lang)), payload, SousChefSuggestions)
    except SousChefUnavailable as exc:
        result["picks"] = [{"recipe": c, "why": "", "substitutions": []} for c in candidates[:MAX_PICKS]]
        result["error"] = str(exc)
        return result
    assert isinstance(answer, SousChefSuggestions)
    by_id = {c["id"]: c for c in candidates}
    seen: set[str] = set()
    for pick in answer.picks:
        if pick.recipe_id not in by_id or pick.recipe_id in seen:
            continue
        seen.add(pick.recipe_id)
        missing_keys = {food_key(m) for m in by_id[pick.recipe_id]["missing"]}
        subs = [s.model_dump() for s in pick.substitutions if food_key(s.missing) in missing_keys or not missing_keys]
        result["picks"].append({"recipe": by_id[pick.recipe_id], "why": pick.why, "substitutions": subs})
        if len(result["picks"]) >= MAX_PICKS:
            break
    result["reply"] = answer.reply
    result["ai"] = True
    return result


_INVENT_PROMPT = """You are Sous Chef. Invent ONE new, realistic home-cooking recipe.

Use mainly the ingredients in "on_hand"; you may add pantry basics (salt, pepper, oil, water,
common spices) and at most 3 other ingredients. Match the user's taste, shown by "style"
(tags and dish names from their saved recipes) and their "message".

Return the recipe JSON schema. Set is_recipe=true and rejection_reason="".
Every ingredient needs food, quantity, unit (use "" when not applicable), notes and raw.
Give realistic quantities. recipeYield is like "4 servings". datePublished may be "".
Write name, description, steps, foods and notes in {language}.
"""


def invent(*, username: str | None, on_hand: list[str], message: str = "",
           diets: list[str] | None = None, max_minutes: int | None = None) -> dict:
    from chef import postprocess_recipe
    from recipe_schema import RecipeExtraction, recipe_dict_from_extraction

    have = [h for h in on_hand if str(h).strip()][:60]
    with connect() as conn:
        tags = [r["name"] for r in conn.execute(
            "SELECT t.name, COUNT(*) AS n FROM recipe_tags rt JOIN tags t ON t.id = rt.tag_id"
            " GROUP BY t.id ORDER BY n DESC LIMIT 15"
        )]
        names = [r["name"] for r in conn.execute(
            "SELECT r.name FROM recipes r LEFT JOIN user_recipe_meta m ON m.recipe_id = r.id AND m.username = ?"
            " ORDER BY COALESCE(m.favorite, 0) DESC, COALESCE(m.rating, 0) DESC, r.created_at DESC LIMIT 15",
            (username or "",),
        )]
    payload = {
        "on_hand": have, "message": message[:1000],
        "style": {"tags": tags, "dishes": names},
        "constraints": {"diets": diets or [], "max_minutes": max_minutes},
    }
    answer = _ask(_INVENT_PROMPT.format(language=_language()), payload, RecipeExtraction)
    assert isinstance(answer, RecipeExtraction)
    if not answer.name.strip() or not answer.recipeIngredients:
        raise SousChefUnavailable("Sous Chef could not come up with a recipe. Try different ingredients.")
    draft = postprocess_recipe(recipe_dict_from_extraction(answer), "")
    draft.pop("video", None)
    draft.pop("url", None)
    return draft


_SUBSTITUTE_PROMPT = """You are Sous Chef. Suggest up to 3 substitutes for "food" in home cooking.
If "recipe" is given, pick substitutes that work in that dish. For each option give what to
use, the amount relative to the original (e.g. "1:1", "3/4 cup per cup") and a short note on
how it changes the result. Write in {language}.
"""


def substitute(food: str, recipe_id: str | None = None) -> list[dict]:
    payload: dict[str, Any] = {"food": food[:200]}
    if recipe_id:
        recipe = get_recipe(recipe_id)
        payload["recipe"] = {
            "name": recipe["name"],
            "ingredients": [i["raw"] for i in recipe["ingredients"]][:60],
        }
    answer = _ask(_SUBSTITUTE_PROMPT.format(language=_language()), payload, SubstituteAnswer)
    assert isinstance(answer, SubstituteAnswer)
    return [o.model_dump() for o in answer.options[:3]]


_PLAN_PROMPT = """You are Sous Chef, planning meals from the user's own recipes.

Fill each slot in "slots" (date + slot name) with one recipe from "candidates", using its exact
id. Do not repeat a recipe within 4 days unless there are too few candidates. Favor variety
(different tags), quick recipes on weekdays, and recipes that share ingredients so shopping
is easier. Respect "message". Breakfast slots get breakfast-like dishes when available.
Leave a slot out rather than forcing a bad fit. "why" is one short phrase.
"summary" is 1-2 sentences. Write in {language}.
"""


def plan_week(*, username: str | None, start: str, end: str, slots: list[str], message: str = "") -> dict:
    from kitchen import mealplan

    open_slots = mealplan.empty_slots(start, end, slots or ["dinner"])
    if not open_slots:
        return {"summary": "", "assignments": []}
    candidates = score_recipes([], username=username, limit=40)
    if not candidates:
        return {"summary": "", "assignments": []}
    payload = {
        "slots": [{"date": d, "weekday": date.fromisoformat(d).strftime("%A"), "slot": s} for d, s in open_slots],
        "candidates": [
            {k: c[k] for k in ("id", "name", "total_minutes", "tags", "missing")} for c in candidates[:40]
        ],
        "message": message[:1000],
    }
    answer = _ask(_PLAN_PROMPT.format(language=_language()), payload, WeekPlan)
    assert isinstance(answer, WeekPlan)
    by_id = {c["id"]: c for c in candidates}
    wanted = set(open_slots)
    used: set[tuple[str, str]] = set()
    assignments = []
    for a in answer.assignments:
        key = (a.date, a.slot)
        if a.recipe_id not in by_id or key not in wanted or key in used:
            continue
        used.add(key)
        c = by_id[a.recipe_id]
        assignments.append({
            "date": a.date, "slot": a.slot, "recipe_id": a.recipe_id, "name": c["name"],
            "image_url": c["image_url"], "why": a.why,
        })
    assignments.sort(key=lambda x: (x["date"], mealplan.SLOTS.index(x["slot"])))
    return {"summary": answer.summary, "assignments": assignments}


def apply_plan(assignments: list[dict], *, servings: Any = None, created_by: str | None = None) -> int:
    from kitchen import mealplan

    added = 0
    for a in assignments[:100]:
        if not isinstance(a, dict):
            continue
        try:
            mealplan.add_entry(
                plan_date=a.get("date"), slot=a.get("slot") or "dinner", recipe_id=a.get("recipe_id"),
                servings=servings, created_by=created_by,
            )
            added += 1
        except (RecipeNotFound, RecipeInvalid, ValueError) as exc:
            logger.info("[Sous Chef] plan entry skipped: %s", exc)
    return added


_REVIEW_PROMPT = """You are Sous Chef reviewing a saved recipe for problems a cook would hit:
an ingredient listed but never used in the steps, an ingredient used in a step but not listed,
a missing step (e.g. never says to cook something), an unclear step, or a quantity missing
where it matters. Report only real problems, each with a concrete suggestion. If the recipe
is fine, return no issues and say so in "summary". Write in {language}.
"""


def review_recipe(recipe_id: str) -> dict:
    recipe = get_recipe(recipe_id)
    payload = {
        "name": recipe["name"],
        "ingredients": [i["raw"] for i in recipe["ingredients"]],
        "steps": [s["text"] for s in recipe["steps"]],
    }
    answer = _ask(_REVIEW_PROMPT.format(language=_language()), payload, RecipeReview)
    assert isinstance(answer, RecipeReview)
    return answer.model_dump()


_TAGS_PROMPT = """Suggest up to 6 short tags for this recipe (cuisine, meal type, main ingredient,
diet, occasion). Prefer tags from "existing_tags" when they fit; only add a new tag when none
fits. Do not repeat "current_tags". Write new tags in {language}.
"""


def suggest_tags(recipe_id: str) -> list[str]:
    recipe = get_recipe(recipe_id)
    with connect() as conn:
        existing = [r["name"] for r in conn.execute("SELECT name FROM tags ORDER BY name LIMIT 300")]
    payload = {
        "name": recipe["name"], "description": recipe["description"][:500],
        "ingredients": [i["food"] for i in recipe["ingredients"]][:40],
        "current_tags": recipe["tags"], "existing_tags": existing,
    }
    answer = _ask(_TAGS_PROMPT.format(language=_language()), payload, TagIdeas)
    assert isinstance(answer, TagIdeas)
    current = {t.casefold() for t in recipe["tags"]}
    canonical = {t.casefold(): t for t in existing}
    out: list[str] = []
    for tag in answer.tags:
        tag = " ".join(tag.split())[:40]
        if not tag or tag.casefold() in current:
            continue
        tag = canonical.get(tag.casefold(), tag)
        if tag not in out:
            out.append(tag)
    return out[:6]


_LINK_PROMPT = """For each step, list the positions of the ingredients it uses. "ingredients" are
numbered by "position"; "steps" by "step". Only use positions that exist. A step that uses no
listed ingredient gets an empty list.
"""


def link_steps(recipe_id: str) -> int:
    recipe = get_recipe(recipe_id)
    positions = {i["position"] for i in recipe["ingredients"]}
    payload = {
        "ingredients": [{"position": i["position"], "food": i["food"]} for i in recipe["ingredients"]],
        "steps": [{"step": s["position"], "text": s["text"]} for s in recipe["steps"]],
    }
    answer = _ask(_LINK_PROMPT, payload, StepLinks)
    assert isinstance(answer, StepLinks)
    steps = {s["position"] for s in recipe["steps"]}
    updated = 0
    with connect() as conn:
        for link in answer.steps:
            if link.step not in steps:
                continue
            refs = sorted({p for p in link.ingredients if p in positions})
            conn.execute(
                "UPDATE recipe_steps SET ingredient_refs = ? WHERE recipe_id = ? AND position = ?",
                (json.dumps(refs), recipe["id"], link.step),
            )
            updated += 1
        conn.commit()
    return updated


def week_bounds(today: date | None = None) -> tuple[str, str]:
    today = today or date.today()
    start = today - timedelta(days=(today.weekday() + 1) % 7)
    return start.isoformat(), (start + timedelta(days=6)).isoformat()
