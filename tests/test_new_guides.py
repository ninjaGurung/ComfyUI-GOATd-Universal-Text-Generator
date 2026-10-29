import importlib.util
import json
from pathlib import Path
import re
import sys
from types import ModuleType
import unittest


ROOT = Path(__file__).resolve().parents[1]
package = ModuleType("new_guides_test_package")
package.__path__ = [str(ROOT)]
sys.modules[package.__name__] = package
spec = importlib.util.spec_from_file_location(package.__name__ + ".prompt_guides", ROOT / "prompt_guides.py")
guides = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guides)


class NewGuideTests(unittest.TestCase):
    def test_minimax_ref2v_selects_its_six_sections_over_shared_t2v_format(self):
        sections = ("subject_definitions", "summary", "retention_analysis", "detailed_description", "overall_soundscape", "non_diegetic_music")
        for references in (("image1",), ("video1",), ("audio1",), ("image1", "video1", "audio1")):
            for json_output in (False, True):
                with self.subTest(references=references, json_output=json_output):
                    combined = guides.combine_system_prompt("Preserve the requested action.", "MiniMax H3 - Ref2V", references, json_output=json_output)
                    self.assertTrue("Generation mode for this request: Ref2V (full-reference mode)." in combined, "Ref2V output contract is missing")
                    contract = combined.split("Generation mode for this request: ", 1)[1]
                    self.assertEqual(sorted(sections, key=contract.index), list(sections))
                    self.assertIn("Use detailed_description instead of integrated_multimodal_description", contract)
                    self.assertIn("Sampled video frames remain observations of their <Video N>", contract)
                    self.assertIn("do not create extra <Picture N> inputs", contract)
                    self.assertIn("six top-level keys" if json_output else "six section labels", contract)

    def test_minimax_ref2v_without_media_keeps_text_only_fallback(self):
        combined = guides.combine_system_prompt("Write a landscape video prompt.", "MiniMax H3 - Ref2V")
        self.assertIn("Use text-only T2VA", combined)
        self.assertNotIn("Generation mode for this request: Ref2V", combined)

    def test_new_guides_are_registered_with_primary_sources_and_valid_json_examples(self):
        for name in ("MOSS Image", "Fibo 2"):
            self.assertIn(name, guides.GUIDE_OPTIONS)
            self.assertTrue(guides.GUIDE_SOURCES[name])
            combined = guides.combine_system_prompt("Preserve user details.", name, json_output=True)
            self.assertTrue(combined.startswith("Preserve user details."))
            for block in re.findall(r"```json\s*(.*?)\s*```", combined, re.S):
                self.assertIsInstance(json.loads(block), dict)

    def test_moss_uses_native_layout_and_still_image_rules(self):
        combined = guides.combine_system_prompt("", "MOSS Image", ("image1",))
        self.assertIn('"bbox_coord_space": "abs_centralized"', combined)
        self.assertIn('"bbox_format": "yxyx"', combined)
        self.assertIn("T2I.", combined)
        self.assertIn("do not claim that MOSS Image consumes these attachments", combined)
        self.assertNotIn("## 1. Task Overview", combined)
        self.assertNotIn("integrated_multimodal_description:", combined)

    def test_fibo_has_one_shared_guide_and_static_edit_schema(self):
        self.assertEqual(sum(name.startswith("Fibo") for name in guides.GUIDE_OPTIONS), 1)
        combined = guides.combine_system_prompt("", "Fibo 2", ("image2", "image10", "audio1"), json_output=True)
        self.assertIn("I2I / Ref2I", combined)
        self.assertIn("@image2 corresponds to <image_1>", combined)
        self.assertIn("@image10 corresponds to <image_2>", combined)
        self.assertNotIn("@audio1 corresponds to <image_3>", combined)
        self.assertIn("final scene statically", combined)
        self.assertIn("without an extra wrapper", guides.combine_system_prompt("", "MOSS Image"))

    def test_fibo_generation_uses_dev_without_invented_sources(self):
        combined = guides.combine_system_prompt("", "Fibo 2", json_output=True)
        self.assertIn("T2I with Fibo 2 Dev", combined)
        self.assertIn("without edit_instruction", combined)
        self.assertIn("Do not invent image references", combined)

    def test_fibo_plain_text_excludes_vgl_schema_for_generation_and_edits(self):
        for references in ((), ("image1",), ("image2", "image10")):
            with self.subTest(references=references):
                combined = guides.combine_system_prompt("Keep the user's details.", "Fibo 2", references, json_output=False)
                self.assertIn("Return only plain-language", combined)
                self.assertIn("Rewrite the rough Prompt Idea", combined)
                self.assertIn("Rephrase descriptive wording", combined)
                self.assertIn("State both what to change and what to preserve", combined)
                self.assertNotIn('"short_description"', combined)
                self.assertNotIn("Return one VGL JSON object", combined)
                self.assertNotIn("Return one final-state VGL JSON object", combined)
                self.assertNotIn("Preserve explicitly supplied details, names, wording, and reference associations.", combined)
        self.assertIn("@image10 corresponds to <image_2>", combined)

    def test_fibo_json_mode_keeps_native_schema_without_prompt_wrapper(self):
        combined = guides.combine_system_prompt("", "Fibo 2", ("image1",), json_output=True)
        self.assertIn("Return one final-state VGL JSON object with edit_instruction", combined)
        self.assertIn('"short_description"', combined)
        self.assertIn("without Markdown fences", combined)

    def test_other_guides_preserve_existing_format_instructions(self):
        for name in ("None", "MOSS Image", "Qwen Image 2.1"):
            self.assertEqual(guides.combine_system_prompt("System", name), guides.combine_system_prompt("System", name, json_output=True))


if __name__ == "__main__":
    unittest.main()
