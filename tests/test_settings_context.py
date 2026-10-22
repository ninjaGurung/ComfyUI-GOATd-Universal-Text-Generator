import ast
from pathlib import Path
from types import SimpleNamespace
import unittest


SOURCE = ast.parse((Path(__file__).resolve().parents[1] / "llm_node.py").read_text(encoding="utf-8"))


def field(name, default=None, **kwargs):
    return SimpleNamespace(id=name, default=default)


BASE_DEFAULTS = dict(provider="OpenRouter", model="", prompt_idea="", system_prompt="", api_key="", api_key_env="", base_url="", thinking="auto", reasoning_effort="auto", max_output_tokens=1024, context_window=0, temperature=0.7, top_p=-1.0, top_k=-1, min_p=-1.0, repetition_penalty=-1.0, frequency_penalty=0.0, presence_penalty=0.0, seed=-1, force_regenerate=False, json_output=False)
namespace = dict(
    io=SimpleNamespace(ComfyNode=object, Boolean=SimpleNamespace(Input=field), Schema=lambda **kwargs: SimpleNamespace(**kwargs), NodeOutput=lambda *values: values),
    UniversalLLM=SimpleNamespace(define_schema=lambda: SimpleNamespace(inputs=[field(name, value) for name, value in BASE_DEFAULTS.items()])),
    TEXT_SETTINGS=SimpleNamespace(Output=field),
    PROMPT_INPUTS={"prompt_idea", "system_prompt", "images", "videos", "audios", "clip"},
)
settings_class = next(node for node in SOURCE.body if isinstance(node, ast.ClassDef) and node.name == "GOATTextGeneratorSettings")
exec(compile(ast.Module(body=[settings_class], type_ignores=[]), "llm_node.py", "exec"), namespace)
Settings = namespace["GOATTextGeneratorSettings"]


class SettingsContextTests(unittest.TestCase):
    def settings(self, **kwargs):
        return Settings.execute(**kwargs)[0]

    def test_toggle_is_appended_and_does_not_enter_output_settings(self):
        ids = [item.id for item in Settings.define_schema().inputs]
        expected = [name for name in BASE_DEFAULTS if name not in namespace["PROMPT_INPUTS"]]
        self.assertEqual(ids, expected + ["remember_context"])
        self.assertNotIn("remember_context", self.settings(remember_context=True))

    def test_new_nodes_have_context_disabled(self):
        self.assertEqual(self.settings()["context_window"], 0)

    def test_enabling_zero_context_uses_8192(self):
        self.assertEqual(self.settings(remember_context=True, context_window=0)["context_window"], 8192)

    def test_enabled_context_preserves_custom_limit(self):
        for limit in (1024, 16384, 32768):
            with self.subTest(limit=limit):
                self.assertEqual(self.settings(remember_context=True, context_window=limit)["context_window"], limit)

    def test_explicit_false_disables_custom_context(self):
        self.assertEqual(self.settings(remember_context=False, context_window=16384)["context_window"], 0)

    def test_legacy_positive_window_without_toggle_remains_enabled(self):
        self.assertEqual(self.settings(context_window=1024)["context_window"], 1024)

    def test_local_defaults_are_preserved(self):
        settings = self.settings()
        self.assertEqual(settings["provider"], "ComfyUI Local")
        self.assertEqual({name: settings[name] for name in ("thinking", "temperature", "top_p", "top_k", "min_p", "repetition_penalty")}, dict(thinking="disabled", temperature=0.7, top_p=0.95, top_k=40, min_p=0.05, repetition_penalty=1.1))

    def test_remote_missing_inputs_use_provider_defaults(self):
        for provider in ("OpenRouter", "Cerebras", "LM Studio", "Ollama", "OpenAI-compatible", "Groq", "OpenCode"):
            with self.subTest(provider=provider):
                settings = self.settings(provider=provider)
                self.assertEqual(settings["thinking"], "auto")
                for name in ("temperature", "top_p", "top_k", "min_p", "repetition_penalty"):
                    self.assertEqual(settings[name], -1)
                self.assertEqual(settings["frequency_penalty"], 0)
                self.assertEqual(settings["presence_penalty"], 0)

    def test_explicit_remote_sampling_values_are_preserved(self):
        supplied = dict(thinking="enabled", temperature=0.3, top_p=0.8, top_k=32, min_p=0.02, repetition_penalty=1.2)
        settings = self.settings(provider="OpenRouter", **supplied)
        self.assertEqual({name: settings[name] for name in supplied}, supplied)


if __name__ == "__main__":
    unittest.main()
