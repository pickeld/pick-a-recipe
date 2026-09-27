"""Tests for unit conversion, the local nutrition table, and the Open Food
Facts fallback for ingredients the table does not know."""

import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import nutrition  # noqa: E402
from nutrition import (  # noqa: E402
    _FOODS,
    _median_by_nutrient,
    _name_is_relevant,
    _off_product_nutrients,
    _search_open_food_facts,
    lookup_recipe_nutrition,
    match_food,
)
from recipe_schema import parse_frame_selection, parse_visual_text  # noqa: E402
from units import canonicalize_unit, normalize_ingredient_units, quantity_to_grams  # noqa: E402


class UnitConversionTests(unittest.TestCase):
    def test_canonicalizes_volume_spellings(self):
        self.assertEqual(canonicalize_unit("tablespoon"), "tbsp")
        self.assertEqual(canonicalize_unit("כף"), "tbsp")
        self.assertEqual(canonicalize_unit("cup"), "cup")
        self.assertEqual(canonicalize_unit("grams"), "g")
        self.assertEqual(canonicalize_unit("גרם"), "g")

    def test_quantity_to_grams(self):
        self.assertAlmostEqual(quantity_to_grams("2", "tbsp"), 30.0)
        self.assertAlmostEqual(quantity_to_grams("1", "cup"), 240.0)
        self.assertAlmostEqual(quantity_to_grams("200", "g"), 200.0)
        self.assertIsNone(quantity_to_grams("1", "handful"))

    def test_normalize_ingredient_units_in_place(self):
        items = [{"food": "flour", "quantity": "1", "unit": "cup", "notes": "", "raw": ""}]
        normalize_ingredient_units(items)
        self.assertEqual(items[0]["unit"], "cup")
        items[0]["unit"] = "tablespoons"
        normalize_ingredient_units(items)
        self.assertEqual(items[0]["unit"], "tbsp")


class NutritionLookupTests(unittest.TestCase):
    """The local table on its own.

    The Open Food Facts fallback is switched off throughout: these assertions
    are about the table, and leaving the lookup on would make them depend on a
    third-party service being reachable and on what it happens to hold today.
    """

    def setUp(self):
        from unittest.mock import patch

        self._no_network = patch.object(
            nutrition, "_lookup_enabled", return_value=False)
        self._no_network.start()
        self.addCleanup(self._no_network.stop)

    def test_matches_hebrew_and_english_foods(self):
        self.assertEqual(match_food("pasta"), "pasta")
        self.assertEqual(match_food("פסטה"), "pasta")
        self.assertEqual(match_food("olive oil"), "olive oil")

    def test_longest_alias_wins_over_short_substring(self):
        self.assertEqual(match_food("extra virgin olive oil"), "olive oil")
        self.assertEqual(match_food("tomato paste"), "tomato paste")
        self.assertNotEqual(match_food("tomato paste"), "tomato")
        self.assertEqual(match_food("grilled chicken breast"), "chicken breast")

    def test_partial_match_returns_table_macros(self):
        recipe = {
            "recipeYield": "1 serving",
            "recipeIngredients": [
                {"food": "pasta", "quantity": "100", "unit": "g", "notes": "", "raw": ""},
                {"food": "mystery spice blend", "quantity": "1", "unit": "tsp", "notes": "", "raw": ""},
            ],
        }
        nutrition = lookup_recipe_nutrition(recipe)
        self.assertIsNotNone(nutrition)
        kcal = float(str(nutrition["calories"]).split()[0])
        self.assertGreater(kcal, 50)

    def test_estimates_per_serving_from_local_table(self):
        recipe = {
            "recipeYield": "2 servings",
            "recipeIngredients": [
                {"food": "pasta", "quantity": "200", "unit": "g", "notes": "", "raw": "200 g pasta"},
                {"food": "olive oil", "quantity": "1", "unit": "tbsp", "notes": "", "raw": "1 tbsp oil"},
            ],
        }
        nutrition = lookup_recipe_nutrition(recipe)
        self.assertIsNotNone(nutrition)
        kcal = float(str(nutrition["calories"]).split()[0])
        # 200 g pasta ≈ 262 kcal + 15 ml oil ≈ 119 kcal → ~381 / 2 servings ≈ 190
        self.assertGreater(kcal, 100)
        self.assertLess(kcal, 300)

    def test_skips_when_most_ingredients_unknown(self):
        recipe = {
            "recipeYield": "2 servings",
            "recipeIngredients": [
                {"food": "mystery spice blend", "quantity": "1", "unit": "tsp", "notes": "", "raw": ""},
                {"food": "another unknown", "quantity": "2", "unit": "g", "notes": "", "raw": ""},
            ],
        }
        self.assertIsNone(lookup_recipe_nutrition(recipe))

    def test_covers_hebrew_staples_beyond_the_original_table(self):
        self.assertGreaterEqual(len(_FOODS), 140)
        self.assertEqual(match_food("חציל"), "eggplant")
        self.assertEqual(match_food("פיתה"), "pita")
        self.assertEqual(match_food("כמון"), "cumin")
        self.assertEqual(match_food("לאבנה"), "labneh")
        self.assertEqual(match_food("בטטה"), "sweet potato")


class OpenFoodFactsTests(unittest.TestCase):
    """The live fallback for ingredients the local table does not carry."""

    def setUp(self):
        # Each test starts from an empty cache and a full request budget, so
        # one test cannot mask a lookup another one expects to make.
        nutrition._off_cache.clear()
        nutrition._off_budget = nutrition._SearchBudget()

    def test_reads_per_100g_nutriments(self):
        parsed = _off_product_nutrients({
            "energy-kcal_100g": 250,
            "proteins_100g": 10,
            "fat_100g": 5,
            "carbohydrates_100g": 40,
            "fiber_100g": 3,
            "sugars_100g": 8,
            "sodium_100g": 0.5,        # grams in OFF, milligrams here
            "cholesterol_100g": 0.02,
        })
        self.assertEqual(parsed[0], 250)
        self.assertEqual(parsed[6], 500)
        self.assertEqual(parsed[7], 20)

    def test_falls_back_to_kilojoules_and_salt(self):
        """Plenty of European products carry kJ and salt rather than kcal and sodium."""
        parsed = _off_product_nutrients({"energy_100g": 418.4, "salt_100g": 2.5})
        self.assertAlmostEqual(parsed[0], 100.0, places=3)
        self.assertAlmostEqual(parsed[6], 1000.0, places=3)

    def test_rejects_a_product_with_no_energy_or_an_impossible_one(self):
        self.assertIsNone(_off_product_nutrients({"proteins_100g": 10}))
        # Above pure fat: a data-entry slip, not a food.
        self.assertIsNone(_off_product_nutrients({"energy-kcal_100g": 5000}))

    def test_median_ignores_products_that_omitted_a_nutrient(self):
        merged = _median_by_nutrient([
            (100, 10, None, None, None, None, None, None),
            (200, None, None, None, None, None, None, None),
            (300, 20, None, None, None, None, None, None),
        ])
        self.assertEqual(merged[0], 200)   # median of 100/200/300
        self.assertEqual(merged[1], 15)    # median of the two that reported it
        self.assertEqual(merged[2], 0.0)   # nobody reported fat

    def test_search_sends_a_user_agent_and_medians_the_results(self):
        """Open Food Facts throttles clients that do not identify themselves."""
        from unittest.mock import patch

        captured = {}

        class _Resp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"hits": [
                    {"product_name": "Dragon fruit jam",
                     "nutriments": {"energy-kcal_100g": 100}},
                    {"product_name": "Dragon fruit jam, organic",
                     "nutriments": {"energy-kcal_100g": 300}},
                    {"product_name": "Dragon fruit jam",
                     "nutriments": {}},  # unusable, must not drag the median
                    {"product_name": "Pickled herring",  # unrelated hit
                     "nutriments": {"energy-kcal_100g": 9000}},
                ]}

        def _fake_get(session, url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return _Resp()

        with patch.object(nutrition, "_lookup_enabled", return_value=True), \
             patch("url_safety.safe_get", _fake_get):
            result = _search_open_food_facts("dragon fruit jam")

        self.assertIn("openfoodfacts.org", captured["url"])
        self.assertIn("Pick-a-Recipe", captured["headers"]["User-Agent"])
        self.assertEqual(captured["params"]["q"], "dragon fruit jam")
        self.assertEqual(result[0], 200)

    def test_result_is_cached_so_a_repeated_food_costs_one_request(self):
        from unittest.mock import patch

        calls = []

        class _Resp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"hits": [
                    {"product_name": "Ackee", "nutriments": {"energy-kcal_100g": 120}},
                ]}

        def _fake_get(session, url, **kwargs):
            calls.append(url)
            return _Resp()

        with patch.object(nutrition, "_lookup_enabled", return_value=True), \
             patch("url_safety.safe_get", _fake_get):
            first = _search_open_food_facts("ackee")
            second = _search_open_food_facts("ACKEE")

        self.assertEqual(first, second)
        self.assertEqual(len(calls), 1)

    def test_disabled_in_settings_means_no_request_at_all(self):
        from unittest.mock import patch

        def _boom(*args, **kwargs):
            raise AssertionError("no request should be made when the lookup is off")

        with patch.object(nutrition, "_lookup_enabled", return_value=False), \
             patch("url_safety.safe_get", _boom):
            self.assertIsNone(_search_open_food_facts("ackee"))

    def test_only_products_that_are_plausibly_the_ingredient_count(self):
        """The search is fuzzy, so a nonsense query still returns real products.

        Costing an ingredient against whatever came back would invent numbers
        the recipe has no basis for; skipping it is much cheaper.
        """
        # One name contains the other, in either direction.
        self.assertTrue(_name_is_relevant("Ackee", "ackee"))
        self.assertTrue(_name_is_relevant("Chicken breast", "chicken breast fillets"))
        self.assertTrue(_name_is_relevant("Harissa paste, hot", "harissa paste"))
        self.assertTrue(_name_is_relevant("\u05d8\u05d7\u05d9\u05e0\u05d4", "\u05d8\u05d7\u05d9\u05e0\u05d4"))
        # Merely sharing a word is not enough.
        self.assertFalse(_name_is_relevant("Organic breakfast cereal", "mystery powder"))
        self.assertFalse(_name_is_relevant("", "ackee"))

    def test_budget_stops_hammering_the_shared_service(self):
        budget = nutrition._SearchBudget(limit=2, window=60.0)
        self.assertTrue(budget.take())
        self.assertTrue(budget.take())
        self.assertFalse(budget.take())

    def test_a_network_failure_is_swallowed(self):
        """Nutrition is a best-effort extra; it must never fail an extraction."""
        from unittest.mock import patch

        def _fail(*args, **kwargs):
            raise OSError("no route to host")

        with patch.object(nutrition, "_lookup_enabled", return_value=True), \
             patch("url_safety.safe_get", _fail):
            self.assertIsNone(_search_open_food_facts("ackee"))


class VisionSchemaTests(unittest.TestCase):
    def test_visual_text_joins_sections(self):
        payload = {
            "title": "Pasta",
            "ingredients_text": "200 g pasta",
            "instructions_text": "Boil.",
            "other_text": "",
        }
        text = parse_visual_text(json.dumps(payload)).as_plain_text()
        self.assertIn("Pasta", text)
        self.assertIn("200 g pasta", text)
        self.assertIn("Boil.", text)

    def test_frame_selection_bounds(self):
        self.assertEqual(parse_frame_selection('{"index": 2}', 5), 2)
        self.assertIsNone(parse_frame_selection('{"index": 9}', 5))
        self.assertIsNone(parse_frame_selection("not json", 5))


if __name__ == "__main__":
    unittest.main()
