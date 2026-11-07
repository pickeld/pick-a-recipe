"""The recipe stays in its own language unless a target language is set."""

import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from config import config  # noqa: E402
from helpers import (  # noqa: E402
    get_recipe_system_prompt,
    get_web_recipe_system_prompt,
    get_yield_nutrition_prompt,
)
from recipe_schema import RecipeExtraction, ensure_target_language  # noqa: E402

PROMPTS = (get_recipe_system_prompt, get_web_recipe_system_prompt)


class RecipeLanguageTest(unittest.TestCase):
    def test_unset_keeps_the_source_language(self):
        with patch.object(config, "_db_config", {}):
            self.assertEqual(config.TARGET_LANGUAGE, "")
            for build in PROMPTS:
                prompt = build()
                self.assertIn("Do NOT translate", prompt)
                self.assertNotIn("Hebrew", prompt)
            self.assertNotIn("in Hebrew.", get_yield_nutrition_prompt())

    def test_target_language_still_translates(self):
        with patch.object(config, "_db_config", {"target_language": "he"}):
            for build in PROMPTS:
                prompt = build()
                self.assertIn("ALL text content MUST be in Hebrew", prompt)
                self.assertNotIn("Do NOT translate", prompt)

    def test_unset_accepts_any_script(self):
        extraction = RecipeExtraction.model_validate({
            "is_recipe": True,
            "rejection_reason": "",
            "name": "Spaghetti al pomodoro",
            "description": "Pasta met tomatensaus, snel en eenvoudig klaar.",
            "datePublished": "2026-11-07",
            "recipeYield": "2",
            "recipeInstructions": [{"text": "Kook de pasta en meng met de saus."}],
            "recipeIngredients": [],
        })
        ensure_target_language(extraction, "")


if __name__ == "__main__":
    unittest.main()
