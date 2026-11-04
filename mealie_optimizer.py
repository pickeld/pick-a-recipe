"""Audits an existing Mealie library and proposes fixes.

Four checks, each producing Finding records that a human reviews before
anything is written back:

- ``duplicate``  clusters of recipes that are the same dish
- ``tags``       taxonomy tags a recipe should have but does not
- ``nutrition``  missing or implausible macros, recomputed from ingredients
- ``quality``    unusable ingredients, gaps in the flow, missing amounts

Nothing in this module writes to Mealie on its own. Scanning only reads;
applying happens through ``apply_finding`` once a reviewer approves.
"""

from __future__ import annotations

import json
import re
import unicodedata
import uuid
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Callable, Iterable

from config import config
from helpers import (
    get_duplicate_check_prompt,
    get_recipe_audit_prompt,
    get_recipe_tagging_prompt,
    setup_logger,
)
from llm_session import LlmSession
from mealie import Mealie, MealieNotConfigured
from nutrition import lookup_recipe_nutrition
from recipe_schema import (
    AUDIT_JSON_SCHEMA,
    DUPLICATE_JSON_SCHEMA,
    NUTRITION_BOUNDS,
    DuplicateVerdict,
    RecipeAudit,
    ValidationError,
    build_tag_suggestion_model,
    parse_duplicate_verdict,
    parse_recipe_audit,
    parse_tag_suggestion,
    to_openai_json_schema,
)

logger = setup_logger(__name__)

CHECKS = ("duplicate", "tags", "nutrition", "quality")

# Macros a recipe is expected to carry. Anything missing from this set is worth
# reporting; the rest (fibre, sugar, sodium) is nice to have.
CORE_MACROS = ("calories", "proteinContent", "fatContent", "carbohydrateContent")

NUTRITION_FIELDS = (
    "calories",
    "proteinContent",
    "fatContent",
    "carbohydrateContent",
    "fiberContent",
    "sugarContent",
    "sodiumContent",
    "cholesterolContent",
)

# The LLM confirms clusters the heuristic found. Below this it is not confident
# enough to justify offering a deletion.
MIN_DUPLICATE_CONFIDENCE = 0.7

_PUNCTUATION = re.compile(r"[^\w\s]", re.UNICODE)
_WHITESPACE = re.compile(r"\s+")

# Words that carry no signal when comparing recipe titles.
_TITLE_NOISE = {
    "recipe", "recipes", "the", "a", "an", "of", "with", "and", "for",
    "easy", "quick", "best", "classic", "homemade", "simple", "my",
    "copy", "copy1", "new", "old", "v2",
    "מתכון", "של", "עם", "קל", "מהיר", "ביתי", "הכי", "טוב",
}


class ScanCancelled(Exception):
    """The reviewer cancelled the scan mid-flight."""


@dataclass
class Finding:
    """One proposed change, pending review."""

    kind: str
    severity: str
    title: str
    detail: str
    proposal: dict[str, Any]
    recipe_slug: str | None = None
    recipe_name: str | None = None
    snapshot: dict[str, Any] | None = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex)


# ===== Normalisation helpers =====


def normalize_title(name: str) -> str:
    """Lowercase, strip accents/punctuation, drop filler words."""
    text = unicodedata.normalize("NFKD", str(name or ""))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = _PUNCTUATION.sub(" ", text.lower())
    tokens = [t for t in _WHITESPACE.split(text) if t and t not in _TITLE_NOISE]
    return " ".join(tokens)


def _ingredient_text(item: dict) -> str:
    """Best available display text for one Mealie ingredient row."""
    food = item.get("food")
    if isinstance(food, dict):
        food_name = str(food.get("name") or "").strip()
    else:
        food_name = str(food or "").strip()

    unit = item.get("unit")
    if isinstance(unit, dict):
        unit_name = str(unit.get("name") or "").strip()
    else:
        unit_name = str(unit or "").strip()

    quantity = item.get("quantity")
    qty_text = ""
    if quantity not in (None, "", 0):
        qty_text = f"{quantity:g}" if isinstance(quantity, (int, float)) else str(quantity)

    parts = [p for p in (qty_text, unit_name, food_name) if p]
    if parts:
        note = str(item.get("note") or "").strip()
        return " ".join(parts) + (f" ({note})" if note else "")

    return str(item.get("originalText") or item.get("note") or "").strip()


def mealie_ingredients(recipe: dict) -> list[dict[str, str]]:
    """Mealie ingredient rows in PAR's structured shape.

    ``nutrition.lookup_recipe_nutrition`` and the LLM prompts both want
    ``{food, quantity, unit, notes, raw}``, which is not how Mealie stores them.
    """
    result: list[dict[str, str]] = []
    for item in recipe.get("recipeIngredient") or []:
        if not isinstance(item, dict):
            text = str(item or "").strip()
            if text:
                result.append({"food": text, "quantity": "", "unit": "",
                               "notes": "", "raw": text})
            continue

        food = item.get("food")
        food_name = str(food.get("name") if isinstance(food, dict) else food or "").strip()
        unit = item.get("unit")
        unit_name = str(unit.get("name") if isinstance(unit, dict) else unit or "").strip()
        quantity = item.get("quantity")
        qty_text = ""
        if quantity not in (None, ""):
            qty_text = f"{quantity:g}" if isinstance(quantity, (int, float)) else str(quantity)
            if qty_text == "0":
                qty_text = ""

        raw = _ingredient_text(item)
        if not food_name:
            # Unparsed row: Mealie kept the whole line in note/originalText.
            food_name = str(item.get("originalText") or item.get("note") or "").strip()

        if not food_name and not raw:
            continue
        result.append({
            "food": food_name,
            "quantity": qty_text,
            "unit": unit_name,
            "notes": str(item.get("note") or "").strip(),
            "raw": raw or food_name,
        })
    return result


def mealie_steps(recipe: dict) -> list[str]:
    """Instruction texts in order."""
    steps: list[str] = []
    for step in recipe.get("recipeInstructions") or []:
        if isinstance(step, dict):
            text = str(step.get("text") or "").strip()
        else:
            text = str(step or "").strip()
        if text:
            steps.append(text)
    return steps


def mealie_tag_names(recipe: dict) -> list[str]:
    names: list[str] = []
    for tag in recipe.get("tags") or []:
        name = tag.get("name") if isinstance(tag, dict) else tag
        if name:
            names.append(str(name))
    return names


def as_par_recipe(recipe: dict) -> dict:
    """Mealie recipe in the Schema.org-ish shape PAR's helpers expect."""
    return {
        "name": recipe.get("name") or "",
        "description": recipe.get("description") or "",
        "recipeYield": recipe.get("recipeYield") or recipe.get("recipeServings") or "",
        "recipeIngredients": mealie_ingredients(recipe),
        "recipeInstructions": [{"text": s} for s in mealie_steps(recipe)],
    }


# ===== Duplicate detection =====


def _ingredient_foods(recipe: dict) -> set[str]:
    foods = set()
    for item in mealie_ingredients(recipe):
        food = normalize_title(item["food"])
        if food:
            foods.add(food)
    return foods


def similarity(a: dict, b: dict) -> float:
    """0..1 similarity between two recipes.

    Identical source URLs short-circuit to 1.0: the same page imported twice is
    the single most common way a Mealie library grows duplicates.
    """
    url_a = str(a.get("orgURL") or "").strip()
    url_b = str(b.get("orgURL") or "").strip()
    if url_a and url_a == url_b:
        return 1.0

    name_score = SequenceMatcher(
        None, normalize_title(a.get("name", "")), normalize_title(b.get("name", ""))
    ).ratio()

    foods_a, foods_b = _ingredient_foods(a), _ingredient_foods(b)
    if foods_a and foods_b:
        overlap = len(foods_a & foods_b) / len(foods_a | foods_b)
    else:
        # No ingredient data on one side: the name is all the evidence there is.
        return name_score * 0.85

    return 0.6 * name_score + 0.4 * overlap


def cluster_duplicates(recipes: list[dict], threshold: float) -> list[list[dict]]:
    """Group recipes into clusters of likely duplicates via union-find."""
    parent = list(range(len(recipes)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[max(ri, rj)] = min(ri, rj)

    for i in range(len(recipes)):
        for j in range(i + 1, len(recipes)):
            if similarity(recipes[i], recipes[j]) >= threshold:
                union(i, j)

    groups: dict[int, list[dict]] = {}
    for idx, recipe in enumerate(recipes):
        groups.setdefault(find(idx), []).append(recipe)
    return [g for g in groups.values() if len(g) > 1]


def completeness_score(recipe: dict) -> int:
    """How much a recipe would cost to lose. Higher wins the keeper slot."""
    score = 0
    score += len(mealie_ingredients(recipe)) * 3
    score += len(mealie_steps(recipe)) * 3
    if any(str(recipe.get("nutrition", {}).get(f) or "").strip()
           for f in NUTRITION_FIELDS if isinstance(recipe.get("nutrition"), dict)):
        score += 10
    if recipe.get("image"):
        score += 5
    if str(recipe.get("description") or "").strip():
        score += 2
    score += len(mealie_tag_names(recipe)) * 2
    if str(recipe.get("orgURL") or "").strip():
        score += 2
    return score


def rank_keeper(cluster: list[dict]) -> list[dict]:
    """Cluster sorted best-keeper first; ties broken by the older recipe."""
    return sorted(
        cluster,
        key=lambda r: (-completeness_score(r), str(r.get("dateAdded") or ""), str(r.get("slug") or "")),
    )


# ===== The optimizer =====


class MealieOptimizer:
    """Runs the audit and applies approved findings."""

    def __init__(self, client: Mealie | None = None,
                 llm: LlmSession | None = None,
                 *, taxonomy: list[str] | None = None,
                 threshold: float | None = None):
        self.client = client or Mealie()
        self._llm = llm
        self.taxonomy = taxonomy if taxonomy is not None else config.MEALIE_TAG_TAXONOMY
        self.threshold = threshold if threshold is not None else config.MEALIE_SIMILARITY_THRESHOLD

    @property
    def llm(self) -> LlmSession:
        # Built lazily so a nutrition-only scan never needs an LLM key.
        if self._llm is None:
            self._llm = LlmSession()
        return self._llm

    # --- scanning ---

    def scan(
        self,
        *,
        checks: Iterable[str] = CHECKS,
        max_recipes: int | None = None,
        on_progress: Callable[[int, int, str], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> list[Finding]:
        """Walk the library and return everything worth reviewing."""
        checks = [c for c in checks if c in CHECKS]
        if not checks:
            return []

        self.client.require_configured()

        def progress(done: int, total: int, message: str) -> None:
            if should_cancel and should_cancel():
                raise ScanCancelled()
            if on_progress:
                on_progress(done, total, message)

        progress(0, 0, "Listing recipes in Mealie…")
        summaries = list(self.client.iter_all_recipes(max_recipes=max_recipes))
        total = len(summaries)
        if not total:
            return []

        # Every check needs full detail (the list endpoint omits steps), so
        # fetch once and reuse.
        recipes: list[dict] = []
        for index, summary in enumerate(summaries, start=1):
            slug = summary.get("slug") or summary.get("id")
            if not slug:
                continue
            progress(index, total + 1, f"Reading {summary.get('name') or slug}")
            try:
                detail = self.client.get_recipe(slug)
            except Exception as exc:
                logger.warning(f"[Optimizer] Could not read '{slug}': {exc}")
                continue
            if detail:
                recipes.append(detail)

        findings: list[Finding] = []
        if "duplicate" in checks:
            progress(total, total + 1, "Looking for duplicate recipes…")
            findings.extend(self.find_duplicates(recipes))

        for index, recipe in enumerate(recipes, start=1):
            name = recipe.get("name") or recipe.get("slug")
            progress(index, total, f"Analysing {name}")
            if "tags" in checks:
                findings.extend(self.check_tags(recipe))
            if "nutrition" in checks:
                findings.extend(self.check_nutrition(recipe))
            if "quality" in checks:
                findings.extend(self.check_quality(recipe))

        progress(total, total, f"Done — {len(findings)} findings")
        return findings

    # --- individual checks ---

    def find_duplicates(self, recipes: list[dict]) -> list[Finding]:
        findings: list[Finding] = []
        for cluster in cluster_duplicates(recipes, self.threshold):
            verdict = self._confirm_duplicate(cluster)
            if not verdict.same_dish or verdict.confidence < MIN_DUPLICATE_CONFIDENCE:
                logger.info(
                    "[Optimizer] Rejected duplicate cluster %s (%s)",
                    [r.get("slug") for r in cluster], verdict.reason,
                )
                continue

            ranked = rank_keeper(cluster)
            members = [
                {
                    "slug": r.get("slug"),
                    "name": r.get("name"),
                    "ingredient_count": len(mealie_ingredients(r)),
                    "step_count": len(mealie_steps(r)),
                    "has_nutrition": bool(_present_nutrition(r)),
                    "has_image": bool(r.get("image")),
                    "tags": mealie_tag_names(r),
                    "source_url": r.get("orgURL") or "",
                    "score": completeness_score(r),
                }
                for r in ranked
            ]
            names = ", ".join(str(m["name"]) for m in members)
            findings.append(Finding(
                kind="duplicate",
                severity="warning",
                recipe_slug=ranked[0].get("slug"),
                recipe_name=ranked[0].get("name"),
                title=f"{len(cluster)} copies of the same dish",
                detail=f"{names} — {verdict.reason}",
                proposal={
                    "members": members,
                    "suggested_keeper": ranked[0].get("slug"),
                    "confidence": verdict.confidence,
                    "reason": verdict.reason,
                },
                snapshot={"recipes": cluster},
            ))
        return findings

    def _confirm_duplicate(self, cluster: list[dict]) -> DuplicateVerdict:
        """Second opinion on a heuristic cluster, so near-misses do not get deleted."""
        urls = [str(r.get("orgURL") or "").strip() for r in cluster]
        if len([u for u in urls if u]) == len(urls) and len(set(urls)) == 1:
            return DuplicateVerdict(
                same_dish=True, confidence=1.0,
                reason="All copies were imported from the same source URL.",
            )

        payload = {
            "recipes": [
                {
                    "name": r.get("name"),
                    "description": r.get("description") or "",
                    "ingredients": [i["raw"] for i in mealie_ingredients(r)],
                    "step_count": len(mealie_steps(r)),
                }
                for r in cluster
            ]
        }
        try:
            response = self.llm.call(
                get_duplicate_check_prompt(),
                json.dumps(payload, ensure_ascii=False),
                schema_name="duplicate_verdict",
                json_schema=DUPLICATE_JSON_SCHEMA,
                schema_model=DuplicateVerdict,
            )
            return parse_duplicate_verdict(response)
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            logger.warning(f"[Optimizer] Duplicate check returned junk: {exc}")
        except Exception as exc:
            logger.warning(f"[Optimizer] Duplicate check failed: {exc}")
        # Unverified clusters are dropped rather than offered for deletion.
        return DuplicateVerdict(same_dish=False, confidence=0.0,
                                reason="Could not verify with the language model.")

    def check_tags(self, recipe: dict) -> list[Finding]:
        if not self.taxonomy:
            return []

        existing = mealie_tag_names(recipe)
        existing_lower = {t.lower() for t in existing}
        candidates = [t for t in self.taxonomy if t.lower() not in existing_lower]
        if not candidates:
            return []

        payload = {
            "name": recipe.get("name"),
            "description": recipe.get("description") or "",
            "ingredients": [i["raw"] for i in mealie_ingredients(recipe)],
            "existing_tags": existing,
        }
        try:
            response = self.llm.call(
                get_recipe_tagging_prompt(candidates),
                json.dumps(payload, ensure_ascii=False),
                schema_name="tag_suggestion",
                json_schema=to_openai_json_schema(build_tag_suggestion_model(candidates)),
                schema_model=build_tag_suggestion_model(candidates),
            )
            suggestions = parse_tag_suggestion(response, candidates)
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            logger.warning(
                f"[Optimizer] Tag suggestion for '{recipe.get('slug')}' was unusable: {exc}"
            )
            return []
        except Exception as exc:
            logger.warning(f"[Optimizer] Tag suggestion failed for '{recipe.get('slug')}': {exc}")
            return []

        if not suggestions:
            return []

        return [Finding(
            kind="tags",
            severity="info",
            recipe_slug=recipe.get("slug"),
            recipe_name=recipe.get("name"),
            title=f"{len(suggestions)} tags to add",
            detail=", ".join(s.tag for s in suggestions),
            proposal={
                "existing_tags": existing,
                "suggested_tags": [{"tag": s.tag, "reason": s.reason} for s in suggestions],
            },
            snapshot={"tags": recipe.get("tags") or []},
        )]

    def check_nutrition(self, recipe: dict) -> list[Finding]:
        current = _present_nutrition(recipe)
        missing = [f for f in CORE_MACROS if f not in current]
        out_of_range = _implausible_fields(current)

        if not missing and not out_of_range:
            return []

        computed = lookup_recipe_nutrition(as_par_recipe(recipe))
        if not computed:
            # Nothing to propose, but a reviewer still wants to know it is empty.
            if not current:
                return [Finding(
                    kind="nutrition",
                    severity="info",
                    recipe_slug=recipe.get("slug"),
                    recipe_name=recipe.get("name"),
                    title="No nutrition, and it cannot be computed",
                    detail=(
                        "None of the ingredients matched the nutrition table, so "
                        "macros would have to be filled in by hand."
                    ),
                    proposal={"current": current, "computed": None, "changes": []},
                    snapshot={"nutrition": recipe.get("nutrition") or {}},
                )]
            return []

        changes = [
            {"field": f, "from": current.get(f, ""), "to": computed[f]}
            for f in NUTRITION_FIELDS
            if computed.get(f) and (f in missing or f in out_of_range)
        ]
        if not changes:
            return []

        if missing and out_of_range:
            title = f"{len(missing)} macros missing, {len(out_of_range)} out of range"
        elif missing:
            title = "Missing nutrition" if len(missing) == len(CORE_MACROS) else f"{len(missing)} macros missing"
        else:
            title = f"{len(out_of_range)} implausible values"

        return [Finding(
            kind="nutrition",
            severity="warning" if out_of_range else "info",
            recipe_slug=recipe.get("slug"),
            recipe_name=recipe.get("name"),
            title=title,
            detail=", ".join(f"{c['field']} → {c['to']}" for c in changes),
            proposal={
                "current": current,
                "computed": {f: computed[f] for f in NUTRITION_FIELDS if computed.get(f)},
                "changes": changes,
                "missing": missing,
                "out_of_range": out_of_range,
            },
            snapshot={"nutrition": recipe.get("nutrition") or {}},
        )]

    def check_quality(self, recipe: dict) -> list[Finding]:
        ingredients = mealie_ingredients(recipe)
        steps = mealie_steps(recipe)
        if not ingredients or not steps:
            return [Finding(
                kind="quality",
                severity="warning",
                recipe_slug=recipe.get("slug"),
                recipe_name=recipe.get("name"),
                title="Recipe is incomplete",
                detail=(
                    "No ingredients recorded." if not ingredients
                    else "No instructions recorded."
                ),
                proposal={"issues": [], "corrected_ingredients": [], "corrected_steps": []},
                snapshot={},
            )]

        payload = {
            "name": recipe.get("name"),
            "ingredients": [i["raw"] for i in ingredients],
            "steps": steps,
        }
        try:
            response = self.llm.call(
                get_recipe_audit_prompt(),
                json.dumps(payload, ensure_ascii=False),
                schema_name="recipe_audit",
                json_schema=AUDIT_JSON_SCHEMA,
                schema_model=RecipeAudit,
            )
            audit = parse_recipe_audit(response)
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            logger.warning(f"[Optimizer] Audit of '{recipe.get('slug')}' was unusable: {exc}")
            return []
        except Exception as exc:
            logger.warning(f"[Optimizer] Audit failed for '{recipe.get('slug')}': {exc}")
            return []

        if not audit.issues:
            return []

        rejection = _reject_lossy_audit(audit, ingredients, steps)
        if rejection:
            logger.warning(
                f"[Optimizer] Discarded audit of '{recipe.get('slug')}': {rejection}"
            )
            return []

        return [Finding(
            kind="quality",
            severity="warning",
            recipe_slug=recipe.get("slug"),
            recipe_name=recipe.get("name"),
            title=f"{len(audit.issues)} problems with the recipe",
            detail=audit.summary,
            proposal={
                "summary": audit.summary,
                "issues": [i.model_dump() for i in audit.issues],
                "original_ingredients": [i["raw"] for i in ingredients],
                "original_steps": steps,
                "corrected_ingredients": audit.corrected_ingredients,
                "corrected_steps": audit.corrected_steps,
            },
            snapshot={
                "recipeIngredient": recipe.get("recipeIngredient") or [],
                "recipeInstructions": recipe.get("recipeInstructions") or [],
            },
        )]

    # --- applying ---

    def apply_finding(self, kind: str, proposal: dict, overrides: dict | None = None) -> dict:
        """Write one approved finding back to Mealie.

        ``overrides`` carries the reviewer's edits: which copy to keep, which
        tags to accept, which issues to act on.
        """
        overrides = overrides or {}
        if kind == "duplicate":
            return self._apply_duplicate(proposal, overrides)
        if kind == "tags":
            return self._apply_tags(proposal, overrides)
        if kind == "nutrition":
            return self._apply_nutrition(proposal, overrides)
        if kind == "quality":
            return self._apply_quality(proposal, overrides)
        raise ValueError(f"Unknown finding kind: {kind}")

    def _apply_duplicate(self, proposal: dict, overrides: dict) -> dict:
        members = proposal.get("members") or []
        slugs = [m.get("slug") for m in members if m.get("slug")]
        keeper = overrides.get("keeper_slug") or proposal.get("suggested_keeper")
        if keeper not in slugs:
            raise ValueError("The recipe to keep is not part of this duplicate group.")

        deleted, failed = [], []
        for slug in slugs:
            if slug == keeper:
                continue
            try:
                if self.client.delete_recipe(slug):
                    deleted.append(slug)
                else:
                    failed.append({"slug": slug, "error": "already gone"})
            except Exception as exc:
                failed.append({"slug": slug, "error": str(exc)})

        if failed and not deleted:
            raise RuntimeError(f"Could not delete any duplicate: {failed[0]['error']}")
        return {"kept": keeper, "deleted": deleted, "failed": failed}

    def _apply_tags(self, proposal: dict, overrides: dict) -> dict:
        slug = overrides["slug"]
        suggested = [s["tag"] for s in proposal.get("suggested_tags") or []]
        accepted = overrides.get("accepted_tags")
        if accepted is None:
            accepted = suggested
        else:
            # Never let a client widen the set beyond what was reviewed.
            accepted = [t for t in accepted if t in suggested]
        if not accepted:
            raise ValueError("No tags were accepted.")

        recipe = self.client.get_recipe(slug)
        if recipe is None:
            raise ValueError(f"Recipe '{slug}' no longer exists in Mealie.")

        catalogue = self.client.get_all_tags()
        merged = list(recipe.get("tags") or [])
        present = {str(t.get("name", "")).lower() for t in merged if isinstance(t, dict)}
        added = []
        for name in accepted:
            if name.lower() in present:
                continue
            tag = self.client.get_or_create_tag(name, catalogue)
            if tag:
                merged.append(tag)
                present.add(name.lower())
                added.append(name)

        if not added:
            return {"slug": slug, "added": []}
        self.client.update_recipe(slug, {"tags": merged})
        return {"slug": slug, "added": added}

    def _apply_nutrition(self, proposal: dict, overrides: dict) -> dict:
        slug = overrides["slug"]
        computed = proposal.get("computed") or {}
        if not computed:
            raise ValueError("There is no computed nutrition to apply.")

        recipe = self.client.get_recipe(slug)
        if recipe is None:
            raise ValueError(f"Recipe '{slug}' no longer exists in Mealie.")

        nutrition = dict(recipe.get("nutrition") or {})
        for change in proposal.get("changes") or []:
            field_name = change.get("field")
            if field_name in computed:
                nutrition[field_name] = computed[field_name]

        settings = dict(recipe.get("settings") or {})
        settings["showNutrition"] = True
        self.client.update_recipe(slug, {"nutrition": nutrition, "settings": settings})
        return {"slug": slug, "nutrition": nutrition}

    def _apply_quality(self, proposal: dict, overrides: dict) -> dict:
        slug = overrides["slug"]
        ingredients = proposal.get("corrected_ingredients") or []
        steps = proposal.get("corrected_steps") or []
        if not ingredients or not steps:
            raise ValueError("This finding has no corrected recipe to apply.")

        recipe = self.client.get_recipe(slug)
        if recipe is None:
            raise ValueError(f"Recipe '{slug}' no longer exists in Mealie.")

        original = recipe.get("recipeIngredient") or []
        if len(ingredients) < len(original):
            raise ValueError("The correction would drop ingredients; refusing to apply.")

        # Rows Mealie already parsed keep their unit/food links; anything the
        # audit added goes in as free text for Mealie to parse on its own.
        rebuilt = []
        for index, line in enumerate(ingredients):
            if index < len(original) and isinstance(original[index], dict):
                row = dict(original[index])
                row["originalText"] = line
                if not row.get("food"):
                    row["note"] = line
                rebuilt.append(row)
            else:
                rebuilt.append({
                    "title": None, "note": line, "unit": None, "food": None,
                    "disableAmount": True, "quantity": None, "originalText": line,
                })

        self.client.update_recipe(slug, {
            "recipeIngredient": rebuilt,
            "recipeInstructions": [
                {"title": "", "summary": "", "text": text, "ingredientReferences": []}
                for text in steps
            ],
        })
        return {"slug": slug, "ingredients": len(rebuilt), "steps": len(steps)}


# ===== Nutrition helpers =====


def _present_nutrition(recipe: dict) -> dict[str, str]:
    """Non-empty nutrition fields currently stored on the recipe."""
    nutrition = recipe.get("nutrition")
    if not isinstance(nutrition, dict):
        return {}
    return {
        f: str(nutrition[f]).strip()
        for f in NUTRITION_FIELDS
        if nutrition.get(f) not in (None, "") and str(nutrition[f]).strip()
    }


def _implausible_fields(current: dict[str, str]) -> list[str]:
    """Fields whose stored value falls outside NUTRITION_BOUNDS."""
    bad = []
    for field_name, value in current.items():
        bounds = NUTRITION_BOUNDS.get(field_name)
        if not bounds:
            continue
        match = re.search(r"[-+]?\d+(?:\.\d+)?", value)
        if not match:
            bad.append(field_name)
            continue
        amount = float(match.group())
        low, high = bounds
        if amount < low or amount > high:
            bad.append(field_name)
    return bad


def _reject_lossy_audit(audit: RecipeAudit, ingredients: list[dict],
                        steps: list[str]) -> str | None:
    """Refuse corrections that throw away content. Returns a reason, or None.

    The prompt forbids deletions, but the guardrail is what actually protects
    the library: an audit is only allowed to add, reorder and reword.
    """
    if len(audit.corrected_ingredients) < len(ingredients):
        return (
            f"dropped ingredients ({len(ingredients)} → "
            f"{len(audit.corrected_ingredients)})"
        )
    if len(audit.corrected_steps) < len(steps):
        return f"dropped steps ({len(steps)} → {len(audit.corrected_steps)})"
    if not any(line.strip() for line in audit.corrected_ingredients):
        return "returned empty ingredients"
    if not any(line.strip() for line in audit.corrected_steps):
        return "returned empty steps"
    return None


__all__ = [
    "CHECKS",
    "Finding",
    "MealieNotConfigured",
    "MealieOptimizer",
    "ScanCancelled",
    "as_par_recipe",
    "cluster_duplicates",
    "completeness_score",
    "mealie_ingredients",
    "mealie_steps",
    "mealie_tag_names",
    "normalize_title",
    "rank_keeper",
    "similarity",
]
