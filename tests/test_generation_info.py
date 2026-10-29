import ast
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import requests

import test_context as context_contracts


namespace = dict(context_contracts.namespace, json=json)
definitions = context_contracts.definitions + [node for node in context_contracts.SOURCE.body if isinstance(node, ast.FunctionDef) and node.name in {"_provider_metadata", "_validate_json", "_reject_json_constant"}]
exec(compile(ast.Module(body=definitions, type_ignores=[]), "llm_node.py", "exec"), namespace)
for class_name, method_name in (("UniversalLLM", "generate"), ("GOATUniversalTextGenerator", "execute")):
    cls = next(node for node in context_contracts.SOURCE.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == method_name)
    method.decorator_list = []
    exec(compile(ast.Module(body=[method], type_ignores=[]), "llm_node.py", "exec"), namespace)


class GenerationInfoTests(unittest.TestCase):
    def setUp(self):
        self.settings = dict(provider="OpenAI-compatible", model="test/model", base_url="", api_key="private-api-key", api_key_env="PRIVATE_KEY_ENV", context_window=0, max_output_tokens=1024, thinking="disabled", reasoning_effort="auto", temperature=0.7, top_p=-1.0, top_k=-1, min_p=-1.0, repetition_penalty=-1.0, frequency_penalty=0.0, presence_penalty=0.0, seed=42, json_output=False)
        fields = [SimpleNamespace(id=key, default=value) for key, value in self.settings.items()]
        namespace["GOATTextGeneratorSettings"] = SimpleNamespace(define_schema=lambda: SimpleNamespace(inputs=fields))
        namespace["combine_system_prompt"] = lambda system, guide, reference_names, **kwargs: system
        namespace["_CONTEXT_MEMORY"] = namespace["_ConversationMemory"]()
        namespace["UniversalLLM"] = SimpleNamespace(generate=lambda **kwargs: namespace["generate"](None, **kwargs))
        namespace["io"] = SimpleNamespace(NodeOutput=lambda *values, ui: SimpleNamespace(result=values, ui=ui))
        namespace["ui"] = SimpleNamespace(PreviewText=lambda text: {"text": (text,)})
        namespace["add_media"] = Mock()
        namespace["parse_stop_sequences"] = lambda value: []
        namespace["generate_local"] = Mock(return_value="generated output")
        self.hidden = SimpleNamespace(prompt=None, unique_id="generator")
        self.response = Mock(status_code=200)
        self.response.__enter__ = Mock(return_value=self.response)
        self.response.__exit__ = Mock(return_value=False)
        self.response.json.return_value = {"choices": [{"message": {"content": "generated output"}, "finish_reason": "length"}], "usage": {"prompt_tokens": 123, "completion_tokens": 45, "total_tokens": 168, "prompt_tokens_details": {"cached_tokens": 10}, "completion_tokens_details": {"reasoning_tokens": 5}}}
        self.post = Mock(return_value=self.response)
        namespace["requests"] = SimpleNamespace(post=self.post, exceptions=requests.exceptions)

    def execute(self, **kwargs):
        options = dict(prompt_idea="private prompt idea", system_prompt="private system prompt", prompt_guide="None", settings=self.settings, context_session="private-session-uuid")
        options.update(kwargs)
        return namespace["execute"](SimpleNamespace(hidden=self.hidden), **options)

    def test_first_text_output_and_zero_context_preview_are_preserved(self):
        output = self.execute()
        self.assertEqual(output.result[0], "generated output")
        self.assertEqual(output.ui, {"text": ("generated output",)})
        self.assertEqual(len(output.result), 2)
        info = json.loads(output.result[1])
        self.assertEqual(info["provider"], "OpenAI-compatible")
        self.assertEqual(info["model"], "test/model")
        self.assertEqual(info["endpoint"], "https://api.openai.com/v1/chat/completions")
        self.assertEqual(info["parameters_sent"]["seed"], 42)
        self.assertEqual(info["output_chars"], len(output.result[0]))
        self.assertGreaterEqual(info["elapsed_seconds"], 0)

    def test_actual_provider_usage_and_finish_reason_are_reported(self):
        info = json.loads(self.execute().result[1])
        self.assertEqual(info["provider_usage"], {"input_tokens": 123, "output_tokens": 45, "total_tokens": 168, "cached_input_tokens": 10, "reasoning_tokens": 5})
        self.assertEqual(info["finish_reason"], "length")

    def test_info_contains_no_credentials_session_or_prompt_contents(self):
        output = self.execute(images={"image1": object(), "image2": None}, videos={"video1": object()})
        info_text = output.result[1]
        for secret in ("private-api-key", "PRIVATE_KEY_ENV", "private prompt idea", "private system prompt", "private-session-uuid", "generated output"):
            self.assertNotIn(secret, info_text)
        info = json.loads(info_text)
        self.assertEqual(info["media"], {"image_sockets": 1, "video_sockets": 1, "audio_sockets": 0})

    def test_context_info_tracks_history_and_new_generation(self):
        self.settings["context_window"] = 1024
        self.execute()
        second = json.loads(self.execute(prompt_idea="next").result[1])
        self.assertEqual(second["context"]["history_turns_sent"], 1)
        self.assertEqual(second["context"]["history_turns_remembered"], 2)
        self.assertEqual(second["context"]["turn_in_retained_context"], 2)
        fresh = json.loads(self.execute(context_reset=1).result[1])
        self.assertEqual(fresh["context"]["history_turns_sent"], 0)
        self.assertEqual(fresh["context"]["history_turns_remembered"], 1)
        self.assertEqual(fresh["context"]["reset_index"], 1)

    def test_local_unknown_random_seed_and_usage_are_not_fabricated(self):
        self.settings.update(provider="ComfyUI Local", seed=-1)
        info = json.loads(self.execute().result[1])
        self.assertEqual(info["model"], "connected CLIP")
        self.assertIsNone(info["provider_usage"])
        self.assertIsNone(info["seed"]["effective"])
        self.assertFalse(info["seed"]["sent"])
        self.assertIsNone(info["endpoint"])

    def test_local_model_name_comes_from_connected_clip_loader(self):
        self.settings["provider"] = "ComfyUI Local"
        for loader_type, model in (("CLIPLoader", "gemma_4.safetensors"), ("CLIPLoaderGGUF", "Qwen3-Q4_K_M.gguf")):
            with self.subTest(loader=loader_type):
                self.hidden.prompt = {
                    "generator": {"inputs": {"clip": ["loader", 0]}},
                    "loader": {"class_type": loader_type, "inputs": {"clip_name": model, "type": "gemma4"}},
                    "unconnected": {"inputs": {"clip_name": "wrong-model.safetensors"}},
                }
                output = self.execute(clip=object())
                self.assertEqual(json.loads(output.result[1])["model"], model)
                self.assertNotIn("wrong-model", output.result[1])

    def test_local_model_name_follows_clip_through_patching_nodes(self):
        self.settings["provider"] = "ComfyUI Local"
        self.hidden.prompt = {
            "generator": {"inputs": {"clip": ["patch", 1]}},
            "patch": {"inputs": {"clip": ["loader", 0], "lora_name": "adapter.safetensors"}},
            "loader": {"inputs": {"clip_name": "models/gemma_4.safetensors"}},
        }
        self.assertEqual(json.loads(self.execute(clip=object()).result[1])["model"], "models/gemma_4.safetensors")

    def test_local_unknown_loader_and_cyclic_links_keep_fallback(self):
        self.settings["provider"] = "ComfyUI Local"
        for inputs in ({}, {"clip": ["loader", 0]}):
            with self.subTest(inputs=inputs):
                self.hidden.prompt = {
                    "generator": {"inputs": {"clip": ["loader", 0]}},
                    "loader": {"inputs": inputs},
                }
                self.assertEqual(json.loads(self.execute(clip=object()).result[1])["model"], "connected CLIP")

    def test_ollama_usage_and_stop_text_redaction(self):
        self.settings["provider"] = "Ollama"
        namespace["parse_stop_sequences"] = lambda value: ["private-stop-text"]
        self.response.json.return_value = {"message": {"content": "generated output"}, "prompt_eval_count": 20, "eval_count": 5, "prompt_eval_cached_count": 7, "done_reason": "stop"}
        output = self.execute()
        info = json.loads(output.result[1])
        self.assertEqual(info["provider_usage"], {"input_tokens": 20, "output_tokens": 5, "cached_input_tokens": 7, "total_tokens": 25})
        self.assertEqual(info["finish_reason"], "stop")
        self.assertEqual(self.post.call_args.kwargs["json"]["options"]["stop"], ["private-stop-text"])
        self.assertNotIn("private-stop-text", output.result[1])

    def test_lm_studio_native_usage_and_unsupported_seed(self):
        self.settings["provider"] = "LM Studio"
        self.response.json.return_value = {"output": [{"type": "message", "content": "generated output"}], "stats": {"input_tokens": 20, "total_output_tokens": 5, "reasoning_output_tokens": 2, "unexpected_secret": "private"}}
        info = json.loads(self.execute().result[1])
        self.assertEqual(info["provider_usage"], {"input_tokens": 20, "output_tokens": 5, "reasoning_tokens": 2, "total_tokens": 25})
        self.assertFalse(info["seed"]["sent"])
        self.assertNotIn("seed", info["parameters_sent"])

    def test_malformed_provider_metadata_does_not_escape_into_info(self):
        self.response.json.return_value["usage"] = {"prompt_tokens": "secret", "completion_tokens": -3, "total_tokens": True, "system_prompt": "private"}
        self.response.json.return_value["choices"][0]["finish_reason"] = "secret arbitrary server text"
        info = json.loads(self.execute().result[1])
        self.assertIsNone(info["provider_usage"])
        self.assertIsNone(info["finish_reason"])

    def test_legacy_public_generate_still_returns_one_text_value(self):
        self.assertEqual(namespace["generate"](None, provider="OpenAI-compatible", model="test", prompt_idea="idea", system_prompt="system"), ("generated output",))


if __name__ == "__main__":
    unittest.main()
