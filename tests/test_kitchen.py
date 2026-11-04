"""Kitchen library, planner, shopping, Sous Chef and importers."""

import base64
import gzip
import io
import json
import os
import sqlite3
import sys
import zipfile
from datetime import date, timedelta

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from kitchen import db, images  # noqa: E402


@pytest.fixture
def kitchen(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_FILE", str(tmp_path / "test.db"))
    monkeypatch.setattr(db, "IMAGES_DIR", str(tmp_path / "images"))
    monkeypatch.setattr(images, "IMAGES_DIR", str(tmp_path / "images"))
    monkeypatch.setattr(db, "_initialized_for", None)
    db.migrate()
    return tmp_path


def _schema(name="Tomato pasta", servings="4 servings", ingredients=None, url="", tags=None):
    return {
        "name": name,
        "description": "A weeknight dinner",
        "recipeYield": servings,
        "totalTime": "PT30M",
        "url": url,
        "keywords": tags or ["Italian", "Dinner"],
        "recipeIngredient": ingredients or [
            "400 g pasta", "2 tbsp olive oil", "3 cloves garlic", "800 g tomatoes", "1 tsp salt",
        ],
        "recipeInstructions": [
            {"@type": "HowToStep", "text": "Boil the pasta for 10 minutes."},
            {"@type": "HowToStep", "text": "Fry the garlic in olive oil, add the tomatoes and simmer 15 minutes."},
        ],
    }


# ===== Schema =====

def test_migrate_is_versioned_and_idempotent(kitchen, monkeypatch):
    with db.connect() as conn:
        assert db.current_version(conn) == len(db.MIGRATIONS)
    monkeypatch.setattr(db, "_initialized_for", None)
    db.migrate()
    with db.connect() as conn:
        rows = conn.execute("SELECT COUNT(*) FROM kitchen_schema_version").fetchone()[0]
    assert rows == len(db.MIGRATIONS)


# ===== Recipes =====

def test_save_scale_and_convert(kitchen):
    from kitchen.recipes import get_recipe, save_from_schema

    rid = save_from_schema(_schema(), source_kind="web")
    full = get_recipe(rid)
    assert full["base_servings"] == 4
    pasta = next(i for i in full["ingredients"] if i["food"] == "pasta")
    assert pasta["amount"] == 400 and pasta["unit"] == "g"

    half = get_recipe(rid, servings=2)
    pasta = next(i for i in half["ingredients"] if i["food"] == "pasta")
    assert pasta["amount"] == 200
    salt = next(i for i in half["ingredients"] if i["food"] == "salt")
    assert salt["amount"] == 0.5
    assert salt["display_quantity"] == "½"

    us = get_recipe(rid, units="us")
    tomatoes = next(i for i in us["ingredients"] if "tomato" in i["food"])
    assert tomatoes["display_unit"] in ("lb", "oz")

    timers = [t for s in full["steps"] for t in s["timers"]]
    assert {t["seconds"] for t in timers} == {600, 900}
    garlic_pos = next(i["position"] for i in full["ingredients"] if i["food"].startswith("garlic"))
    assert garlic_pos in full["steps"][1]["ingredient_refs"]


def test_search_filters_and_user_meta(kitchen):
    from kitchen.recipes import list_recipes, log_cook, save_from_schema, set_user_meta

    pasta = save_from_schema(_schema())
    salad = save_from_schema(_schema(
        name="Chicken salad", tags=["Salad"],
        ingredients=["300 g chicken breast", "1 lettuce", "2 tbsp olive oil"],
    ))
    assert [r["id"] for r in list_recipes(q="chick")["items"]] == [salad]
    assert [r["id"] for r in list_recipes(q="tomatoes")["items"]] == [pasta]
    assert [r["id"] for r in list_recipes(exclude_ingredients=["chicken"])["items"]] == [pasta]
    assert [r["id"] for r in list_recipes(tags=["salad"])["items"]] == [salad]

    set_user_meta("dana", salad, favorite=True, rating=5)
    assert [r["id"] for r in list_recipes(username="dana", favorites_only=True)["items"]] == [salad]
    assert list_recipes(username="noa", favorites_only=True)["items"] == []
    log_cook("dana", pasta)
    assert list_recipes(username="dana", sort="recently_cooked")["items"][0]["id"] == pasta


def test_update_recipe_rejects_unknown_fields(kitchen):
    from kitchen.recipes import get_recipe, save_from_schema, update_recipe

    rid = save_from_schema(_schema())
    update_recipe(rid, {"name": "Better pasta", "servings": 2, "tags": ["Quick"], "id": "hijack"})
    recipe = get_recipe(rid)
    assert recipe["id"] == rid
    assert recipe["name"] == "Better pasta"
    assert recipe["tags"] == ["Quick"]
    assert recipe["base_servings"] == 2


def test_library_target_dedupes_by_url(kitchen):
    from kitchen.library import save_pipeline_recipe
    from kitchen.recipes import get_recipe, list_recipes

    url = "https://example.com/pasta"
    first = save_pipeline_recipe(_schema(url=url), None)
    second = save_pipeline_recipe(_schema(name="Tomato pasta v2", url=url), None)
    assert first == second
    assert list_recipes()["total"] == 1
    assert get_recipe(first)["name"] == "Tomato pasta v2"


def test_images_are_reencoded_and_path_safe(kitchen):
    PIL = pytest.importorskip("PIL.Image")
    buf = io.BytesIO()
    PIL.new("RGB", (3000, 1000), "red").save(buf, format="PNG")
    name = images.store_image_bytes(buf.getvalue())
    assert name.endswith(".jpg")
    with PIL.open(images.image_path(name)) as img:
        assert img.format == "JPEG" and max(img.size) == images.MAX_SIDE
    assert images.image_path("../test.db") is None
    with pytest.raises(images.ImageError):
        images.store_image_bytes(b"<svg></svg>")


# ===== Shopping =====

def test_shopping_merges_recipes(kitchen):
    from kitchen import shopping
    from kitchen.recipes import save_from_schema

    a = save_from_schema(_schema())
    b = save_from_schema(_schema(name="Tomato soup", ingredients=["400 g tomatoes", "1 onion", "1 l water"]))
    list_id = shopping.default_list_id()
    shopping.add_recipe(list_id, a)
    shopping.add_recipe(list_id, b)

    items = {i["food"]: i for i in shopping.list_items(list_id)}
    assert items["tomatoes"]["quantity"] == 1200
    assert len(items["tomatoes"]["sources"]) == 2
    assert not items["garlic"]["excluded"]
    assert "water" not in items
    groups = [g["aisle"] for g in shopping.grouped(list(items.values()))]
    assert groups.index("produce") < groups.index("pantry")


def test_shopping_last_write_wins(kitchen):
    from kitchen import shopping

    list_id = shopping.default_list_id()
    item = shopping.add_item(list_id, food="2 kg tomatoes")
    row = shopping.list_items(list_id)[0]
    assert row["food"] == "tomatoes" and row["quantity"] == 2 and row["unit"] == "kg"

    newer = shopping.update_item(item, {"checked": True})
    assert not newer.get("stale")
    older = shopping.update_item(item, {"checked": False, "client_ts": "2000-01-01T00:00:00Z"})
    assert older.get("stale")
    assert shopping.list_items(list_id)[0]["checked"]
    future = shopping.update_item(item, {"note": "ripe", "client_ts": "2999-01-01T00:00:00Z"})
    assert future["updated_at"] < "2999"


# ===== Meal plan =====

def test_plan_leftovers_and_generate(kitchen):
    from kitchen import mealplan, shopping
    from kitchen.recipes import save_from_schema

    rid = save_from_schema(_schema())
    today = date.today()
    cook = mealplan.add_entry(plan_date=today.isoformat(), slot="dinner", recipe_id=rid, servings=4)
    mealplan.add_entry(plan_date=(today + timedelta(days=1)).isoformat(), slot="lunch", leftover_of=cook, servings=2)
    plan = mealplan.cooking_plan(today.isoformat(), (today + timedelta(days=6)).isoformat())
    assert len(plan) == 1 and plan[0]["servings"] == 6

    list_id = shopping.default_list_id()
    totals = shopping.generate_from_plan(list_id, today.isoformat(), (today + timedelta(days=6)).isoformat())
    assert totals["recipes"] == 1
    pasta = next(i for i in shopping.list_items(list_id) if i["food"] == "pasta")
    assert pasta["quantity"] == 600

    empty = mealplan.empty_slots(today.isoformat(), (today + timedelta(days=1)).isoformat(), ["dinner", "lunch"])
    assert (today.isoformat(), "dinner") not in empty
    assert ((today + timedelta(days=1)).isoformat(), "lunch") not in empty
    assert (today.isoformat(), "lunch") in empty


def test_ics_feed_is_escaped(kitchen):
    from kitchen import mealplan

    mealplan.add_entry(plan_date=date.today().isoformat(), slot="dinner", title="Pizza, night; fun\\")
    feed = mealplan.ics_feed("https://chef.example")
    assert "BEGIN:VCALENDAR" in feed and "\r\n" in feed
    assert "Pizza\\, night\\; fun\\\\" in feed


# ===== Tokens =====

def test_tokens_are_hashed(kitchen):
    from kitchen import tokens
    from kitchen.recipes import save_from_schema

    rid = save_from_schema(_schema())
    token = tokens.create_share_link(rid, "dana")
    assert tokens.shared_recipe(token) == rid
    assert tokens.shared_recipe(token[:-1] + ("A" if token[-1] != "A" else "B")) is None
    assert tokens.shared_recipe("../../etc") is None
    with sqlite3.connect(db.DB_FILE) as conn:
        stored = [r[0] for r in conn.execute("SELECT token_hash FROM share_links")]
    assert token not in stored and stored == [tokens.token_hash(token)]

    ics = tokens.rotate_ics_token("dana")
    assert tokens.ics_user(ics) == "dana"
    assert tokens.ics_user(tokens.rotate_ics_token("dana")) == "dana"
    assert tokens.ics_user(ics) is None


# ===== Sous Chef =====

def _library_for_souschef():
    from kitchen.recipes import save_from_schema

    return {
        "pasta": save_from_schema(_schema()),
        "chicken": save_from_schema(_schema(
            name="Roast chicken", ingredients=["1 chicken", "2 lemons", "4 cloves garlic", "1 tsp salt"],
        )),
        "eggplant": save_from_schema(_schema(
            name="Roasted eggplant", ingredients=["2 eggplants", "3 tbsp tahini", "1 lemon", "salt"],
        )),
    }


def test_scoring_ranks_by_coverage_and_diet(kitchen):
    from kitchen import souschef

    ids = _library_for_souschef()
    ranked = souschef.score_recipes(["garlic", "lemon", "chicken"], seed=1)
    assert ranked[0]["id"] == ids["chicken"]
    assert ranked[0]["missing"] == []
    assert "salt" not in ranked[0]["have"]

    veg = souschef.score_recipes(["garlic", "lemon", "eggplant"], diets=["vegetarian"], seed=1)
    veg_ids = [r["id"] for r in veg]
    assert ids["chicken"] not in veg_ids and ids["eggplant"] in veg_ids

    assert souschef.score_recipes(["chocolate"]) == []
    assert len(souschef.score_recipes([])) == 3


def test_message_words_find_recipes(kitchen, monkeypatch):
    from kitchen import souschef
    from kitchen.recipes import save_from_schema

    ids = _library_for_souschef()
    ids["asparagus"] = save_from_schema(_schema(
        name="פסטה ירוקה", ingredients=["200 גרם אספרגוס", "250 גרם פסטה", "1 לימון"],
    ))

    assert souschef.message_terms("משהו עם ואספרגוס לארוחת ערב") == ["אספרגוס", "ואספרגוס"]
    assert "with" not in souschef.message_terms("something with eggplant")

    seen = {}

    def fake_ask(system, payload, model):
        seen["system"] = system
        seen["top"] = payload["candidates"][0]
        return souschef.SousChefSuggestions(reply="יש", picks=[])

    monkeypatch.setattr(souschef, "_ask", fake_ask)
    souschef.suggest(username="dana", on_hand=[], message="ואספרגוס", lang="he")
    assert seen["top"]["id"] == ids["asparagus"]
    assert seen["top"]["mentioned"] == ["אספרגוס"]
    assert "in Hebrew." in seen["system"]

    # A recipe the message names still shows up when the typed ingredients miss it.
    ranked = souschef.score_recipes(["chicken"], mentions=["eggplant"], seed=1)
    assert {r["id"] for r in ranked} == {ids["chicken"], ids["eggplant"]}
    assert ranked[0]["id"] == ids["eggplant"]


def test_suggest_drops_ids_outside_candidates(kitchen, monkeypatch):
    from kitchen import souschef

    ids = _library_for_souschef()

    def fake_ask(system, payload, model):
        assert {c["id"] for c in payload["candidates"]} <= set(ids.values())
        return souschef.SousChefSuggestions(reply="Try these", picks=[
            souschef.SousChefPick(recipe_id="made-up", why="?", substitutions=[]),
            souschef.SousChefPick(recipe_id=ids["chicken"], why="You have it all", substitutions=[]),
            souschef.SousChefPick(recipe_id=ids["chicken"], why="again", substitutions=[]),
        ])

    monkeypatch.setattr(souschef, "_ask", fake_ask)
    result = souschef.suggest(username="dana", on_hand=["chicken", "lemon", "garlic"])
    assert result["ai"] is True
    assert [p["recipe"]["id"] for p in result["picks"]] == [ids["chicken"]]


def test_suggest_without_provider_falls_back(kitchen, monkeypatch):
    from kitchen import souschef

    _library_for_souschef()

    def unavailable(*_a, **_k):
        raise souschef.SousChefUnavailable("No AI provider is configured")

    monkeypatch.setattr(souschef, "_ask", unavailable)
    result = souschef.suggest(username="dana", on_hand=["garlic"])
    assert result["ai"] is False and result["error"]
    assert result["picks"]


def test_plan_week_only_fills_open_slots(kitchen, monkeypatch):
    from kitchen import mealplan, souschef

    ids = _library_for_souschef()
    start = date.today()
    end = start + timedelta(days=1)
    mealplan.add_entry(plan_date=start.isoformat(), slot="dinner", recipe_id=ids["pasta"])

    def fake_ask(system, payload, model):
        return souschef.WeekPlan(summary="ok", assignments=[
            souschef.PlanAssignment(date=start.isoformat(), slot="dinner", recipe_id=ids["chicken"], why="taken"),
            souschef.PlanAssignment(date=end.isoformat(), slot="dinner", recipe_id=ids["chicken"], why="ok"),
            souschef.PlanAssignment(date=end.isoformat(), slot="dinner", recipe_id=ids["pasta"], why="dupe"),
            souschef.PlanAssignment(date=end.isoformat(), slot="lunch", recipe_id="nope", why="bad"),
        ])

    monkeypatch.setattr(souschef, "_ask", fake_ask)
    plan = souschef.plan_week(username="dana", start=start.isoformat(), end=end.isoformat(), slots=["dinner"])
    assert [(a["date"], a["recipe_id"]) for a in plan["assignments"]] == [(end.isoformat(), ids["chicken"])]
    assert souschef.apply_plan(plan["assignments"]) == 1


# ===== Importers =====

def test_text_duration():
    from kitchen.importers import text_duration

    assert text_duration("PT20M") == "PT20M"
    assert text_duration(45) == "PT45M"
    assert text_duration(90) == "PT1H30M"
    assert text_duration("1 hour 15 minutes") == "PT1H15M"
    assert text_duration("20 דקות") == "PT20M"
    assert text_duration("") == "" and text_duration("soon") == ""


def test_mealie_and_tandoor_conversion(kitchen):
    from kitchen.importers import mealie_to_schema, tandoor_to_schema
    from kitchen.recipes import get_recipe, save_from_schema

    mealie = mealie_to_schema({
        "name": "Shakshuka", "recipeServings": 2, "totalTime": "30 minutes", "orgURL": "https://x.test/s",
        "tags": [{"name": "Breakfast"}],
        "recipeIngredient": [
            {"quantity": 4, "unit": None, "food": {"name": "eggs"}, "note": "", "originalText": "4 eggs"},
            {"quantity": 1, "unit": {"name": "can"}, "food": {"name": "tomatoes"}, "note": "crushed"},
        ],
        "recipeInstructions": [{"text": "Simmer the sauce."}, {"text": "Crack in the eggs."}],
        "nutrition": {"calories": "300", "proteinContent": None},
    })
    assert mealie["totalTime"] == "PT30M"
    assert mealie["recipeIngredients"][1]["raw"] == "1 can tomatoes crushed"
    rid = save_from_schema(mealie, source_kind="import")
    assert get_recipe(rid)["tags"] == ["Breakfast"]

    tandoor = tandoor_to_schema({
        "name": "Hummus", "servings": 6, "working_time": 15, "waiting_time": 60,
        "keywords": [{"name": "Dip"}],
        "steps": [{"name": "", "instruction": "Blend everything.", "ingredients": [
            {"is_header": True, "note": "Base"},
            {"amount": 400, "unit": {"name": "g"}, "food": {"name": "chickpeas"}, "note": "cooked"},
            {"amount": 0, "no_amount": True, "unit": None, "food": {"name": "salt"}, "note": "to taste"},
        ]}],
        "nutrition": {"calories": 210, "proteins": 8},
    })
    assert tandoor["totalTime"] == "PT1H15M"
    assert tandoor["recipeYield"] == "6 servings"
    assert len(tandoor["recipeIngredient"]) == 2
    assert tandoor["nutrition"]["proteinContent"] == "8"


def _paprika_entry(name, photo=None):
    data = {"name": name, "ingredients": "2 cups flour\n1 tsp salt", "directions": "Mix.\nBake 20 min.",
            "servings": "4", "categories": ["Baking"], "source_url": ""}
    if photo:
        data["photo_data"] = base64.b64encode(photo).decode()
    return gzip.compress(json.dumps(data).encode())


def test_paprika_archive(kitchen):
    from kitchen.importers import ImportFailed, read_paprika

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Bread.paprikarecipe", _paprika_entry("Bread"))
        zf.writestr("Cake.paprikarecipe", _paprika_entry("Cake"))
        zf.writestr("junk.txt", b"not gzip")
    recipes = list(read_paprika(buf.getvalue()))
    assert [s["name"] for s, _ in recipes] == ["Bread", "Cake"]
    assert recipes[0][0]["recipeCategory"] == ["Baking"]

    single = list(read_paprika(_paprika_entry("Solo")))
    assert single[0][0]["name"] == "Solo"
    with pytest.raises(ImportFailed):
        list(read_paprika(b"definitely not an archive"))


def test_paprika_zip_bomb_is_capped(kitchen, monkeypatch):
    from kitchen import importers

    monkeypatch.setattr(importers, "MAX_ENTRY_BYTES", 1024)
    bomb = gzip.compress(json.dumps({"name": "x", "ingredients": "a" * 100_000}).encode())
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("bomb.paprikarecipe", bomb)
    assert list(importers.read_paprika(buf.getvalue())) == []


def test_schema_json_discovery():
    from kitchen.importers import clean_schema, find_schema_recipes

    doc = {"@graph": [{"@type": "WebPage"}, {"@type": ["Recipe"], "name": "Soup", "image": "x.jpg",
                                             "recipeIngredient": ["1 l water"], "totalTime": "1 hour"}]}
    found = find_schema_recipes(doc)
    assert len(found) == 1
    cleaned = clean_schema(found[0])
    assert "image" not in cleaned and cleaned["totalTime"] == "PT1H"


def test_remote_images_stay_on_configured_host():
    from kitchen.importers import _same_host

    assert _same_host("http://mealie.lan:9000/api/media/x.webp", "http://mealie.lan:9000")
    assert not _same_host("http://evil.test/x.webp", "http://mealie.lan:9000")
    assert not _same_host("file:///etc/passwd", "http://mealie.lan:9000")
