import importlib.util
from pathlib import Path
import sys
from types import ModuleType
import unittest


ROOT = Path(__file__).resolve().parents[1]
package = ModuleType("pika_guide_tests")
package.__path__ = [str(ROOT)]
sys.modules[package.__name__] = package
spec = importlib.util.spec_from_file_location(package.__name__ + ".prompt_guides", ROOT / "prompt_guides.py")
guides = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = guides
spec.loader.exec_module(guides)


class PikaGuideTests(unittest.TestCase):
    def test_pika_is_in_minimax_family_and_uses_supplied_writing_guidance(self):
        self.assertIn("MiniMax H3 - Pika", guides.GUIDE_OPTIONS)
        combined = guides.combine_system_prompt("Keep user details.", "MiniMax H3 - Pika")
        self.assertTrue(combined.startswith("Keep user details."))
        self.assertIn("# Pika H3 Flex prompt guidance", combined)
        self.assertIn("fully_preserved", combined)
        self.assertIn("<d>[Chinese]", combined)
        self.assertNotIn("## 6. Running the examples with the CLI", combined)
        self.assertNotIn("pika-h3-pro --", combined)

    def test_text_only_mode_excludes_reference_fields_and_image_alignment(self):
        combined = guides.combine_system_prompt("", "MiniMax H3 - Pika")
        mode = combined.split("Generation mode for this request: ", 1)[1]
        self.assertIn("T2VA. Use these three fields in order", mode)
        self.assertIn("Omit image alignment and reference-only fields", mode)

    def test_references_keep_roles_and_choose_endpoint_mode_only_when_explicit(self):
        for references in (("image1",), ("image2", "image4"), ("video1", "audio2")):
            combined = guides.combine_system_prompt("", "MiniMax H3 - Pika", references)
            mode = combined.split("Generation mode for this request: ", 1)[1]
            self.assertIn("Choose FL2VA only when the user explicitly assigns", mode)
            self.assertIn("Otherwise use Ref2VA with these six fields in order", mode)
            self.assertIn("do not create extra <Picture N> inputs", mode)
            self.assertIn("do not assume sampled videos provide an audio track", mode)
            self.assertIn("Available attached media inputs: " + ", ".join(references), combined)

    def test_json_uses_native_mode_keys_and_keeps_alignment_inside_object(self):
        combined = guides.combine_system_prompt("", "MiniMax H3 - Pika", ("image1",), json_output=True)
        mode = combined.split("Generation mode for this request: ", 1)[1]
        self.assertIn("Return one JSON object", mode)
        self.assertIn("field names as top-level keys", mode)
        self.assertIn("FL2VA alignment instruction inside integrated_multimodal_description", mode)


if __name__ == "__main__":
    unittest.main()
