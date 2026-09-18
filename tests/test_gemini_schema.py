"""Guard for Gemini structured-output schemas (regression test for issue #29).

The Gemini Developer API rejects ``additionalProperties`` in ``response_schema``
with ``400 INVALID_ARGUMENT`` ("Unknown name \"additional_properties\""). Pydantic
models declared with ``extra="forbid"`` emit exactly that key, and the google-genai
SDK forwards it verbatim, so every Gemini structured call blew up mid-pipeline.

``to_gemini_json_schema`` is the single chokepoint that scrubs the key. These tests
run on every PR so that adding a new Gemini-backed model (or wiring a call site to a
raw Pydantic model) cannot silently reintroduce the crash.
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from recipe_schema import (  # noqa: E402
    FrameSelection,
    RecipeExtraction,
    VisualTextExtraction,
    YieldNutritionEstimate,
    to_gemini_json_schema,
)

# Every Pydantic model handed to Gemini as a response_schema. Extend this list
# whenever a new Gemini structured-output call is added.
GEMINI_RESPONSE_MODELS = [
    RecipeExtraction,
    YieldNutritionEstimate,
    VisualTextExtraction,
    FrameSelection,
]


def _find_additional_properties(node) -> bool:
    """True if ``additionalProperties`` appears anywhere in a JSON schema tree."""
    if isinstance(node, dict):
        if "additionalProperties" in node:
            return True
        return any(_find_additional_properties(v) for v in node.values())
    if isinstance(node, list):
        return any(_find_additional_properties(v) for v in node)
    return False


class GeminiSchemaContractTests(unittest.TestCase):
    def test_helper_strips_additional_properties_for_every_model(self):
        for model in GEMINI_RESPONSE_MODELS:
            with self.subTest(model=model.__name__):
                schema = to_gemini_json_schema(model)
                self.assertFalse(
                    _find_additional_properties(schema),
                    f"{model.__name__} schema still contains additionalProperties",
                )

    def test_helper_preserves_the_field_contract(self):
        # Scrubbing must not weaken the structured-output contract.
        schema = to_gemini_json_schema(RecipeExtraction)
        self.assertEqual(schema.get("type"), "object")
        required = schema.get("required") or []
        self.assertIn("is_recipe", required)
        self.assertIn("recipeIngredients", required)
        self.assertIn("recipeInstructions", required)

    def test_raw_model_would_have_triggered_the_bug(self):
        # Documents *why* the helper exists: extra="forbid" emits the key that
        # Gemini rejects. If this ever stops being true, the guard can relax.
        raw = RecipeExtraction.model_json_schema()
        self.assertTrue(_find_additional_properties(raw))


class GeminiSdkPayloadTests(unittest.TestCase):
    """End-to-end: the schema the SDK actually serialises must be Gemini-safe."""

    def test_sdk_wire_schema_has_no_additional_properties(self):
        try:
            from google.genai import _transformers as gemini_transformers
        except Exception as exc:  # pragma: no cover - SDK internals moved
            self.skipTest(f"google-genai schema transformer unavailable: {exc}")

        transform = getattr(gemini_transformers, "t_schema", None)
        if transform is None:  # pragma: no cover - SDK internals moved
            self.skipTest("google-genai t_schema transformer unavailable")

        for model in GEMINI_RESPONSE_MODELS:
            with self.subTest(model=model.__name__):
                wire_schema = transform(None, to_gemini_json_schema(model))
                dumped = wire_schema.model_dump(exclude_none=True, mode="json")
                self.assertNotIn(
                    "additional_properties",
                    __import__("json").dumps(dumped),
                    f"{model.__name__} would still send additional_properties to Gemini",
                )


if __name__ == "__main__":
    unittest.main()
