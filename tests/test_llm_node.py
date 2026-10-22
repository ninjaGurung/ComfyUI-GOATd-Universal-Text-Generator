# Author: ninjaGurung
# GitHub: https://github.com/ninjaGurung
import ast
import base64
from fractions import Fraction
import importlib.util
from io import BytesIO
import json
import math
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import wave

import av
import gguf
from PIL import Image
import requests
import torch


NODE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(NODE_DIR.parents[1]))
from comfy_api.latest import InputImpl, Types, io
from comfy_api.latest import _io

COMFY_DIR = Path(_io.__file__).resolve().parents[2]

spec = importlib.util.spec_from_file_location("universal_llm_test_package", NODE_DIR / "__init__.py", submodule_search_locations=[str(NODE_DIR)])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
node_module = sys.modules[spec.name + ".llm_node"]
media_module = sys.modules[spec.name + ".media"]
local_module = sys.modules[spec.name + ".local"]
UniversalLLM = package.NODE_CLASS_MAPPINGS["UniversalLLM"]
Generator = package.NODE_CLASS_MAPPINGS["GOATUniversalTextGenerator"]
Settings = package.NODE_CLASS_MAPPINGS["GOATTextGeneratorSettings"]
guides_module = sys.modules[spec.name + ".prompt_guides"]


class UniversalLLMTests(unittest.TestCase):
    def setUp(self):
        self.node = UniversalLLM()
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.post_patch = patch.object(node_module.requests, "post")
        self.post = self.post_patch.start()
        self.addCleanup(self.post_patch.stop)
        self.response = Mock(status_code=200)
        self.response.__enter__ = Mock(return_value=self.response)
        self.response.__exit__ = Mock(return_value=False)
        self.response.json.return_value = {"choices": [{"message": {"content": "A cinematic cabin."}}]}
        self.post.return_value = self.response

    def generate(self, provider="OpenRouter", **kwargs):
        inputs = {"provider": provider, "model": "test/model", "prompt_idea": "A cabin", "system_prompt": "Return a prompt."}
        if provider in ("OpenRouter", "Cerebras", "Groq"):
            inputs["api_key"] = "test-secret"
        inputs.update(kwargs)
        return self.node.generate(**inputs)

    def body(self):
        return self.post.call_args.kwargs["json"]

    def test_registration_and_input_contract(self):
        self.assertEqual(package.NODE_DISPLAY_NAME_MAPPINGS["UniversalLLM"], "Universal LLM")
        self.assertEqual(tuple(UniversalLLM.RETURN_TYPES), ("STRING",))
        self.assertEqual(tuple(UniversalLLM.RETURN_NAMES), ("text",))
        self.assertTrue(callable(getattr(self.node, UniversalLLM.FUNCTION)))
        inputs = {}
        schema = UniversalLLM.INPUT_TYPES()
        for fields in (schema["required"], schema["optional"]):
            for name, (_, config) in fields.items():
                if "default" in config:
                    inputs[name] = config["default"]
        inputs.update(model="test/model", api_key="test-secret")
        self.assertEqual(self.node.generate(**inputs), ("A cinematic cabin.",))
        self.assertEqual(schema["required"]["prompt_idea"][0], "STRING")
        self.assertEqual(schema["required"]["system_prompt"][0], "STRING")

    def test_openrouter_payload_and_output(self):
        self.assertEqual(self.generate(thinking="enabled", reasoning_effort="high", context_window=8192, top_k=40, min_p=0.05, repetition_penalty=1.1), ("A cinematic cabin.",))
        self.assertEqual(self.post.call_args.args[0], "https://openrouter.ai/api/v1/chat/completions")
        body = self.body()
        self.assertEqual(body["reasoning"], {"enabled": True, "effort": "high"})
        self.assertEqual(body["max_completion_tokens"], 1024)
        self.assertEqual(body["repetition_penalty"], 1.1)
        self.assertEqual(body["top_k"], 40)
        self.assertEqual(body["min_p"], 0.05)
        self.assertEqual(body["messages"], [{"role": "system", "content": "Return a prompt."}, {"role": "user", "content": "A cabin"}])
        self.assertNotIn("context_length", body)
        self.assertFalse(body["stream"])
        self.assertFalse(self.post.call_args.kwargs["allow_redirects"])

    def test_answer_flows_into_existing_clip_encode(self):
        source = ast.parse((COMFY_DIR / "nodes.py").read_text(encoding="utf-8"))
        clip_node = next(item for item in source.body if isinstance(item, ast.ClassDef) and item.name == "CLIPTextEncode")
        encode = next(item for item in clip_node.body if isinstance(item, ast.FunctionDef) and item.name == "encode")
        namespace = {}
        exec(compile(ast.Module(body=[encode], type_ignores=[]), "CLIPTextEncode.encode", "exec"), namespace)
        clip = Mock()
        clip.tokenize.return_value = {"tokens": [1, 2]}
        clip.encode_from_tokens_scheduled.return_value = "conditioning"
        text, = self.generate()
        self.assertEqual(namespace["encode"](None, clip, text), ("conditioning",))
        clip.tokenize.assert_called_once_with("A cinematic cabin.")
        clip.encode_from_tokens_scheduled.assert_called_once_with({"tokens": [1, 2]})

    def test_opencode_preset_and_service_account_key(self):
        with patch.dict(os.environ, {"OPENCODE_API_KEY": "oc_sk_service-secret"}):
            self.assertEqual(self.generate("OpenCode", model="deepseek-v4.1-flash-free", thinking="auto"), ("A cinematic cabin.",))
        self.assertEqual(self.post.call_args.args[0], "https://opencode.ai/inference/openai/v1/chat/completions")
        self.assertEqual(self.post.call_args.kwargs["headers"]["Authorization"], "Bearer oc_sk_service-secret")
        self.assertEqual(self.body()["max_tokens"], 1024)
        self.assertNotIn("max_completion_tokens", self.body())
        self.assertNotIn("reasoning_effort", self.body())
        self.generate("OpenCode", thinking="disabled")
        self.assertNotIn("reasoning_effort", self.body())
        self.generate("OpenCode", model="deepseek-v4.1-flash-free", api_key="", base_url="https://opencode.ai/inference/openai/v1/chat/completions")
        self.assertNotIn("Authorization", self.post.call_args.kwargs["headers"])
        self.assertEqual(self.post.call_args.args[0], "https://opencode.ai/inference/openai/v1/chat/completions")

    def test_bearer_prefix_is_not_duplicated(self):
        for provider, base in (("OpenRouter", ""), ("OpenCode", ""), ("OpenAI-compatible", "https://example.test/v1")):
            with self.subTest(provider=provider):
                self.generate(provider, base_url=base, api_key="  Bearer pasted-secret  ")
                self.assertEqual(self.post.call_args.kwargs["headers"]["Authorization"], "Bearer pasted-secret")

    def test_provider_error_details_are_redacted(self):
        for provider, detail in (("OpenRouter", "Budget limit exceeded"), ("OpenCode", "Wrong workspace"), ("OpenAI-compatible", "Model access denied")):
            with self.subTest(provider=provider):
                self.response.status_code = 403
                self.response.json.return_value = {"error": {"message": detail + "\nBearer diagnostic-secret " + "x" * 1000}}
                with self.assertRaises(RuntimeError) as caught:
                    self.generate(provider, api_key="diagnostic-secret", base_url="https://example.test/v1")
                message = str(caught.exception)
                self.assertIn(detail, message)
                self.assertIn("HTTP 403", message)
                self.assertIn("[redacted]", message)
                self.assertNotIn("diagnostic-secret", message)
                self.assertNotIn("\n", message)
                self.assertLess(len(message), 450)

    def test_opencode_error_formats(self):
        self.response.status_code = 403
        for result, message in (({"_tag": "Forbidden"}, "Forbidden"), ({"type": "error", "error": {"type": "FreeTierError", "message": "OpenCode's free tier can only be used from within OpenCode."}}, "free tier can only be used from within OpenCode")):
            with self.subTest(result=result):
                self.response.json.return_value = result
                with self.assertRaisesRegex(RuntimeError, message):
                    self.generate("OpenCode")

    def test_openrouter_error_in_success_response(self):
        self.response.json.return_value = {"error": {"code": 403, "message": "Budget limit exceeded. test-secret"}}
        with self.assertRaisesRegex(RuntimeError, "Budget limit exceeded") as caught:
            self.generate()
        self.assertNotIn("test-secret", str(caught.exception))

    def test_openrouter_upstream_error_details(self):
        for status, detail in ((429, "Model is temporarily rate-limited upstream"), (404, "No endpoints found matching your data policy"), (200, "Provider is unavailable")):
            with self.subTest(status=status):
                self.response.status_code = status
                self.response.json.return_value = {"error": {"message": "Provider returned error", "metadata": {"raw": detail + "\nBearer test-secret " + "x" * 1000}}}
                with self.assertRaises(RuntimeError) as caught:
                    self.generate()
                message = str(caught.exception)
                self.assertIn("Provider returned error", message)
                self.assertIn(detail, message)
                self.assertIn("[redacted]", message)
                self.assertNotIn("test-secret", message)
                self.assertNotIn("\n", message)
                self.assertLess(len(message), 500)

    def test_openrouter_retry_delay_without_automatic_retries(self):
        self.response.status_code = 429
        self.response.json.return_value = {"error": {"message": "Rate limit exceeded"}}
        self.response.headers = {"Retry-After": "60"}
        with self.assertRaisesRegex(RuntimeError, "Retry after 60 seconds"):
            self.generate()
        self.post.assert_called_once()

    def test_malformed_upstream_error_metadata(self):
        self.response.status_code = 429
        for metadata in (None, [], {"raw": None}, {"raw": {}}, {"raw": ""}):
            with self.subTest(metadata=metadata):
                self.response.json.return_value = {"error": {"message": "Rate limit exceeded", "metadata": metadata}}
                with self.assertRaisesRegex(RuntimeError, "Rate limit exceeded"):
                    self.generate()

    def test_opencode_images_and_video_frames(self):
        self.generate("OpenCode", images={"image1": torch.zeros(1, 2, 2, 3)}, videos={"video1": self.make_video()}, video_frame_count=2)
        parts = self.body()["messages"][-1]["content"]
        self.assertEqual(sum(part["type"] == "image_url" for part in parts), 3)
        self.assertFalse(any(part["type"] == "video_url" for part in parts))
        with self.assertRaisesRegex(ValueError, "does not accept audio"):
            self.generate("OpenCode", audios={"audio1": {"waveform": torch.zeros(1, 1, 8), "sample_rate": 16000}})

    def test_cerebras_only_supported_parameters(self):
        self.generate("Cerebras", model="qwen-3.8-27b", thinking="disabled", top_k=40, min_p=0.1, repetition_penalty=1.1, frequency_penalty=0.2, presence_penalty=0.3, seed=42)
        self.assertEqual(self.post.call_args.args[0], "https://api.cerebras.ai/v1/chat/completions")
        body = self.body()
        self.assertEqual(body["reasoning_effort"], "none")
        self.assertEqual(body["max_completion_tokens"], 1024)
        self.assertEqual(body["frequency_penalty"], 0.2)
        self.assertEqual(body["presence_penalty"], 0.3)
        self.assertEqual(body["seed"], 42)
        for name in ("top_k", "min_p", "repetition_penalty", "context_window"):
            self.assertNotIn(name, body)

    def test_cerebras_enabled_default_effort(self):
        self.generate("Cerebras", thinking="enabled")
        self.assertEqual(self.body()["reasoning_effort"], "medium")

    def test_cerebras_gpt_oss_thinking_modes(self):
        for mode, effort, expected in (("auto", "high", None), ("enabled", "auto", "medium"), ("enabled", "low", "low"), ("enabled", "medium", "medium"), ("enabled", "high", "high"), ("disabled", "high", "low")):
            with self.subTest(mode=mode, effort=effort):
                self.generate("Cerebras", model="gpt-oss-120b", thinking=mode, reasoning_effort=effort, max_output_tokens=2048, seed=311826475, stop_sequences="<|im_end|>")
                body = self.body()
                self.assertEqual(body.get("reasoning_effort"), expected)
                self.assertEqual(body.get("reasoning_format"), "hidden" if mode == "disabled" else None)
                self.assertEqual(body["max_completion_tokens"], 2048)
                self.assertEqual(body["seed"], 311826475)
                self.assertEqual(body["stop"], ["<|im_end|>"])

    def test_provider_thinking_matrix(self):
        cases = (
            ("OpenRouter", "openai/gpt-oss-120b", "reasoning", None, {"enabled": True, "effort": "low"}, {"enabled": False}),
            ("Cerebras", "gpt-oss-120b", "reasoning_effort", None, "low", "low"),
            ("Cerebras", "qwen-3.8-27b", "reasoning_effort", None, "low", "none"),
            ("Groq", "qwen/qwen3.8-27b", "reasoning_effort", None, "low", "none"),
            ("Groq", "llama-3.3-70b-versatile", "reasoning_effort", None, None, None),
            ("LM Studio", "local/model", "reasoning", None, "low", "off"),
            ("Ollama", "gpt-oss:20b", "think", None, "low", False),
            ("OpenAI-compatible", "server/model", "reasoning_effort", None, "low", "none"),
        )
        for provider, model, field, *expected in cases:
            for mode, value in zip(("auto", "enabled", "disabled"), expected):
                with self.subTest(provider=provider, model=model, mode=mode):
                    self.response.json.return_value = {"output": [{"type": "message", "content": "Answer"}]} if provider == "LM Studio" else {"message": {"content": "Answer"}} if provider == "Ollama" else {"choices": [{"message": {"content": "Answer", "reasoning": "Private"}}]}
                    self.post.reset_mock()
                    self.assertEqual(self.generate(provider, model=model, thinking=mode, reasoning_effort="low", base_url="http://localhost:8080/v1" if provider == "OpenAI-compatible" else ""), ("Answer",))
                    body = self.body()
                    if value is None:
                        self.assertNotIn(field, body)
                    else:
                        self.assertEqual(body[field], value)
                    self.assertNotIn("disable_reasoning", body)
                    self.post.assert_called_once()

    def test_provider_sampling_matrix(self):
        common = {"temperature": 0.4, "top_p": 0.8}
        seeded = {**common, "seed": 42, "stop": ["END"]}
        penalized = {**seeded, "frequency_penalty": 0.2, "presence_penalty": 0.3}
        cases = (
            ("OpenRouter", {**penalized, "top_k": 10, "min_p": 0.1, "repetition_penalty": 1.2, "max_completion_tokens": 99}),
            ("Cerebras", {**penalized, "max_completion_tokens": 99}),
            ("Groq", {**seeded, "max_completion_tokens": 99}),
            ("LM Studio", {**common, "top_k": 10, "min_p": 0.1, "repeat_penalty": 1.2, "max_output_tokens": 99, "context_length": 2048}),
            ("Ollama", {"options": {**seeded, "top_k": 10, "min_p": 0.1, "repeat_penalty": 1.2, "num_predict": 99, "num_ctx": 2048}}),
            ("OpenAI-compatible", {**penalized, "max_tokens": 99}),
        )
        for provider, expected in cases:
            with self.subTest(provider=provider):
                self.response.json.return_value = {"output": [{"type": "message", "content": "Answer"}]} if provider == "LM Studio" else {"message": {"content": "Answer"}} if provider == "Ollama" else {"choices": [{"message": {"content": "Answer"}}]}
                self.generate(provider, temperature=0.4, top_p=0.8, top_k=10, min_p=0.1, repetition_penalty=1.2, frequency_penalty=0.2, presence_penalty=0.3, seed=42, stop_sequences='["END"]', max_output_tokens=99, context_window=2048, base_url="http://localhost:8080/v1" if provider == "OpenAI-compatible" else "")
                self.assertEqual({name: value for name, value in self.body().items() if name not in ("model", "messages", "input", "system_prompt", "stream", "store")}, expected)
                self.assertFalse(self.post.call_args.kwargs["allow_redirects"])

    def test_provider_auth_and_failure_matrix(self):
        for provider, (_, env_name) in node_module.PROVIDERS.items():
            base = "http://localhost:8080/v1" if provider == "OpenAI-compatible" else ""
            for status in (400, 401, 403, 404, 429, 500, 302):
                with self.subTest(provider=provider, status=status), patch.dict(os.environ, {env_name: "matrix-secret"}):
                    self.response.status_code = status
                    self.response.json.return_value = {"error": {"message": "Invalid parameter. Bearer matrix-secret"}}
                    self.post.reset_mock()
                    with self.assertRaisesRegex(RuntimeError, f"HTTP {status}") as error:
                        self.generate(provider, api_key="", base_url=base)
                    self.assertNotIn("matrix-secret", str(error.exception))
                    self.assertEqual(self.post.call_args.kwargs["headers"]["Authorization"], "Bearer matrix-secret")
                    self.post.assert_called_once()
                    self.assertFalse(self.post.call_args.kwargs["allow_redirects"])
        self.response.status_code = 200

    def test_cerebras_error_is_actionable_bounded_and_redacted(self):
        self.response.status_code = 400
        self.response.json.return_value = {"error": {"message": 'reasoning_effort must be low, medium, or high.\nBearer test-secret ' + "x" * 1000}}
        with self.assertRaises(RuntimeError) as error:
            self.generate("Cerebras", model="gpt-oss-120b")
        message = str(error.exception)
        self.assertIn("reasoning_effort must be low, medium, or high.", message)
        self.assertIn("[redacted]", message)
        self.assertNotIn("test-secret", message)
        self.assertNotIn("\n", message)
        self.assertLess(len(message), 450)

    def test_cerebras_malformed_errors_keep_http_context(self):
        self.response.status_code = 400
        for result in ([], None, {"error": None}, {"error": {"message": ["unexpected"]}}, {"error": {"message": ""}}):
            with self.subTest(result=result), self.assertRaisesRegex(RuntimeError, "HTTP 400"):
                self.response.json.return_value = result
                self.generate("Cerebras")
        self.response.json.side_effect = requests.exceptions.JSONDecodeError("invalid", "<html>", 0)
        with self.assertRaisesRegex(RuntimeError, "HTTP 400"):
            self.generate("Cerebras")


    def test_groq_preset_supported_sampling_and_key_environment(self):
        with patch.dict(os.environ, {"GROQ_API_KEY": "groq-env-secret"}):
            self.generate("Groq", api_key="", model="llama-3.3-70b-versatile", temperature=0.4, top_p=0.8, top_k=10, min_p=0.1, repetition_penalty=1.2, frequency_penalty=0.5, presence_penalty=0.5, seed=42, context_window=8192, stop_sequences='["\\nUser:", "END"]')
        self.assertEqual(self.post.call_args.args[0], "https://api.groq.com/openai/v1/chat/completions")
        self.assertEqual(self.post.call_args.kwargs["headers"]["Authorization"], "Bearer groq-env-secret")
        body = self.body()
        self.assertEqual(body["max_completion_tokens"], 1024)
        self.assertEqual(body["temperature"], 0.4)
        self.assertEqual(body["top_p"], 0.8)
        self.assertEqual(body["seed"], 42)
        self.assertEqual(body["stop"], ["\nUser:", "END"])
        for name in ("top_k", "min_p", "repetition_penalty", "frequency_penalty", "presence_penalty", "context_window", "reasoning_effort", "reasoning_format", "include_reasoning"):
            self.assertNotIn(name, body)
        with patch.dict(os.environ, {"CUSTOM_GROQ_KEY": "custom-secret"}):
            self.generate("Groq", api_key="", api_key_env="CUSTOM_GROQ_KEY")
        self.assertEqual(self.post.call_args.kwargs["headers"]["Authorization"], "Bearer custom-secret")
        self.generate("Groq", api_key="direct-secret", base_url="https://example.test/openai/v1/chat/completions")
        self.assertEqual(self.post.call_args.args[0], "https://example.test/openai/v1/chat/completions")
        self.assertEqual(self.post.call_args.kwargs["headers"]["Authorization"], "Bearer direct-secret")

    def test_groq_gpt_oss_reasoning_controls(self):
        self.response.json.return_value = {"choices": [{"message": {"content": "Final answer", "reasoning": "Private reasoning"}}]}
        for model in ("openai/gpt-oss-20b", "openai/gpt-oss-120b"):
            with self.subTest(model=model):
                self.assertEqual(self.generate("Groq", model=model), ("Final answer",))
                self.assertFalse(self.body()["include_reasoning"])
                self.assertNotIn("reasoning_effort", self.body())
                self.assertNotIn("reasoning_format", self.body())
                self.generate("Groq", model=model, thinking="enabled", reasoning_effort="low")
                self.assertEqual(self.body()["reasoning_effort"], "low")
                self.generate("Groq", model=model, thinking="enabled")
                self.assertEqual(self.body()["reasoning_effort"], "medium")
                self.post.reset_mock()
                with self.assertRaisesRegex(ValueError, "cannot disable thinking"):
                    self.generate("Groq", model=model, thinking="disabled")
                self.post.assert_not_called()

    def test_groq_qwen_reasoning_and_nonreasoning_models(self):
        for model, mode, effort, expected in (("qwen/qwen3.8-27b", "auto", "auto", None), ("qwen/qwen3.8-27b", "disabled", "auto", "none"), ("qwen/qwen3.8-27b", "enabled", "high", "high"), ("qwen/qwen3.8-27b", "enabled", "auto", "medium"), ("qwen/qwen3-32b", "enabled", "high", "default"), ("qwen/qwen3-32b", "disabled", "auto", "none")):
            with self.subTest(model=model, mode=mode, effort=effort):
                self.generate("Groq", model=model, thinking=mode, reasoning_effort=effort)
                self.assertEqual(self.body().get("reasoning_effort"), expected)
                self.assertEqual(self.body()["reasoning_format"], "hidden")
                self.assertNotIn("include_reasoning", self.body())
        self.generate("Groq", model="minimaxai/minimax-m2.7", thinking="auto")
        self.assertEqual(self.body()["reasoning_format"], "hidden")
        self.assertNotIn("reasoning_effort", self.body())
        self.generate("Groq", model="llama-3.3-70b-versatile", thinking="enabled")
        self.assertNotIn("reasoning_effort", self.body())
        self.assertNotIn("reasoning_format", self.body())

    def test_groq_vision_uses_image_content(self):
        self.generate("Groq", model="vision-model", prompt_idea="Describe @image1", images={"image1": torch.zeros(1, 2, 2, 3)})
        content = self.body()["messages"][-1]["content"]
        self.assertEqual(content[-1]["type"], "image_url")
        self.assertTrue(content[-1]["image_url"]["url"].startswith("data:image/png;base64,"))

    def test_lm_studio_native_context_thinking_and_answer(self):
        self.response.json.return_value = {"output": [{"type": "reasoning", "content": "private thought"}, {"type": "message", "content": "A snowy cabin."}, {"type": "message", "content": "Golden sunset."}]}
        self.assertEqual(self.generate("LM Studio", thinking="enabled", reasoning_effort="low", context_window=8192, seed=42, stop_sequences="STOP", frequency_penalty=0.3, presence_penalty=0.4, top_k=20, min_p=0.05, repetition_penalty=1.2), ("A snowy cabin.\nGolden sunset.",))
        self.assertEqual(self.post.call_args.args[0], "http://127.0.0.1:1234/api/v1/chat")
        body = self.body()
        self.assertEqual(body["input"], "A cabin")
        self.assertEqual(body["system_prompt"], "Return a prompt.")
        self.assertEqual(body["context_length"], 8192)
        self.assertEqual(body["reasoning"], "low")
        self.assertEqual(body["max_output_tokens"], 1024)
        self.assertEqual(body["repeat_penalty"], 1.2)
        self.assertFalse(body["store"])
        for name in ("seed", "stop", "messages", "frequency_penalty", "presence_penalty"):
            self.assertNotIn(name, body)
        self.assertNotIn("Authorization", self.post.call_args.kwargs["headers"])

    def test_ollama_native_options_and_answer(self):
        self.response.json.return_value = {"message": {"content": "A snowy cabin.", "thinking": "private thought"}, "done": True}
        self.assertEqual(self.generate("Ollama", thinking="enabled", context_window=16000, max_output_tokens=9000, temperature=0, top_p=0.9, top_k=20, min_p=0.05, repetition_penalty=1.2, seed=42, stop_sequences="END\n\n stop "), ("A snowy cabin.",))
        self.assertEqual(self.post.call_args.args[0], "http://127.0.0.1:11434/api/chat")
        body = self.body()
        self.assertIs(body["think"], True)
        self.assertEqual(body["options"], {"num_predict": 9000, "num_ctx": 16000, "temperature": 0, "top_p": 0.9, "top_k": 20, "min_p": 0.05, "repeat_penalty": 1.2, "seed": 42, "stop": ["END", " stop "]})

    def test_ollama_effort(self):
        self.response.json.return_value = {"message": {"content": "Answer"}}
        self.generate("Ollama", thinking="enabled", reasoning_effort="high")
        self.assertEqual(self.body()["think"], "high")

    def test_disabled_thinking_mappings(self):
        for provider, field, expected, result in (
            ("OpenRouter", "reasoning", {"enabled": False}, {"choices": [{"message": {"content": "Answer"}}]}),
            ("LM Studio", "reasoning", "off", {"output": [{"type": "message", "content": "Answer"}]}),
            ("Ollama", "think", False, {"message": {"content": "Answer"}}),
        ):
            with self.subTest(provider=provider):
                self.response.json.return_value = result
                self.generate(provider, thinking="disabled", reasoning_effort="high")
                self.assertEqual(self.body()[field], expected)

    def test_custom_openai_compatible(self):
        self.generate("OpenAI-compatible", base_url="http://localhost:8080/v1/", seed=7, thinking="enabled", reasoning_effort="low")
        self.assertEqual(self.post.call_args.args[0], "http://localhost:8080/v1/chat/completions")
        self.assertEqual(self.body()["max_tokens"], 1024)
        self.assertEqual(self.body()["reasoning_effort"], "low")
        self.assertNotIn("Authorization", self.post.call_args.kwargs["headers"])

    def test_endpoint_variants(self):
        cases = (
            ("OpenRouter", "https://example.test/proxy/v1/chat/completions/", "https://example.test/proxy/v1/chat/completions"),
            ("Ollama", "http://localhost:11434/api/", "http://localhost:11434/api/chat"),
            ("Ollama", "http://localhost:11434/api/chat", "http://localhost:11434/api/chat"),
            ("Ollama", "http://localhost:11434/proxy", "http://localhost:11434/proxy/api/chat"),
            ("LM Studio", "http://localhost:1234/api/v1", "http://localhost:1234/api/v1/chat"),
            ("LM Studio", "http://localhost:1234/api/v1/chat", "http://localhost:1234/api/v1/chat"),
            ("LM Studio", "http://localhost:1234/proxy", "http://localhost:1234/proxy/api/v1/chat"),
        )
        for provider, base, expected in cases:
            with self.subTest(base=base):
                self.assertEqual(node_module._endpoint(provider, base), expected)

    def test_invalid_urls_never_send_keys(self):
        for url in ("file:///tmp/key", "ftp://example.test", "http://user:secret@example.test/v1", "https://example.test/v1?key=secret", "https://example.test/v1#secret"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.generate("OpenAI-compatible", base_url=url)
        self.post.assert_not_called()

    def test_openai_compatible_default_url(self):
        self.generate("OpenAI-compatible")
        self.assertEqual(self.post.call_args.args[0], "https://api.openai.com/v1/chat/completions")

    def test_model_and_provider_validation(self):
        for inputs in ({"model": " "}, {"provider": "Unknown"}, {"thinking": "invalid"}, {"reasoning_effort": "invalid"}):
            with self.subTest(inputs=inputs), self.assertRaises(ValueError):
                self.generate(**inputs)
        self.post.assert_not_called()

    def test_hosted_key_required(self):
        for provider in ("OpenRouter", "Cerebras", "Groq"):
            with self.subTest(provider=provider), self.assertRaisesRegex(ValueError, "API key"):
                self.generate(provider, api_key="")
        self.post.assert_not_called()

    def test_environment_key_and_override(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "env-secret", "MY_LLM_KEY": "other-secret"}):
            self.generate(api_key="")
            self.assertEqual(self.post.call_args.kwargs["headers"]["Authorization"], "Bearer env-secret")
            self.generate(api_key="", api_key_env="MY_LLM_KEY")
            self.assertEqual(self.post.call_args.kwargs["headers"]["Authorization"], "Bearer other-secret")
            self.generate(api_key="direct-secret", api_key_env="MY_LLM_KEY")
            self.assertEqual(self.post.call_args.kwargs["headers"]["Authorization"], "Bearer direct-secret")

    def test_local_auth_optional_but_supported(self):
        self.response.json.return_value = {"message": {"content": "Answer"}}
        self.generate("Ollama", api_key="local-secret")
        self.assertEqual(self.post.call_args.kwargs["headers"]["Authorization"], "Bearer local-secret")

    def test_netrc_does_not_replace_selected_api_key(self):
        self.post_patch.stop()
        with patch("requests.sessions.get_netrc_auth", return_value=("unrelated-user", "unrelated-secret")), patch("requests.sessions.Session.send", return_value=self.response) as send:
            self.generate()
        prepared = send.call_args.args[0]
        self.assertEqual(prepared.headers["Authorization"], "Bearer test-secret")

    def test_keyless_local_request_does_not_send_netrc_credentials(self):
        self.post_patch.stop()
        self.response.json.return_value = {"message": {"content": "Answer"}}
        with patch("requests.sessions.get_netrc_auth", return_value=("unrelated-user", "unrelated-secret")), patch("requests.sessions.Session.send", return_value=self.response) as send:
            self.generate("Ollama")
        self.assertNotIn("Authorization", send.call_args.args[0].headers)

    def test_provider_defaults_and_empty_system(self):
        self.generate(system_prompt="", temperature=-1, top_p=-1, seed=-1, thinking="auto", reasoning_effort="high")
        body = self.body()
        self.assertEqual(body["messages"], [{"role": "user", "content": "A cabin"}])
        for name in ("temperature", "top_p", "seed", "reasoning", "reasoning_effort", "frequency_penalty", "presence_penalty", "stop"):
            self.assertNotIn(name, body)

    def test_default_local_context_is_omitted(self):
        for provider, result in (("Ollama", {"message": {"content": "Answer"}}), ("LM Studio", {"output": [{"type": "message", "content": "Answer"}]})):
            with self.subTest(provider=provider):
                self.response.json.return_value = result
                self.generate(provider, system_prompt="")
                self.assertNotIn("context_length", self.body())
                self.assertNotIn("num_ctx", self.body().get("options", {}))
                self.assertNotIn("system_prompt", self.body())

    def test_content_parts_exclude_reasoning(self):
        self.response.json.return_value = {"choices": [{"message": {"content": [{"type": "text", "text": "Cabin. "}, {"type": "reasoning", "text": "private thought"}, {"type": "output_text", "text": "Snow."}], "reasoning": "private thought"}}]}
        self.assertEqual(self.generate(), ("Cabin. Snow.",))

    def test_reasoning_only_provider_answers_cannot_complete_blank(self):
        for message in ({"content": ""}, {"content": " \n"}, {"reasoning": "unfinished"}, {"content": None, "reasoning_content": "private"}):
            with self.subTest(message=message), self.assertRaisesRegex(RuntimeError, "Increase max_output_tokens"):
                self.response.json.return_value = {"choices": [{"message": message, "finish_reason": "length"}]}
                self.generate()

    def test_split_settings_schema_and_ui_output(self):
        schema = Generator.GET_SCHEMA()
        self.assertEqual(schema.display_name, "GOAT'd Text Generator 🐐")
        self.assertTrue(schema.is_output_node)
        self.assertEqual([field.id for field in schema.inputs], ["prompt_idea", "system_prompt", "prompt_guide", "images", "videos", "audios", "clip", "settings", "context_session", "context_reset"])
        self.assertEqual(next(field for field in schema.inputs if field.id == "prompt_guide").default, "None")
        values = Settings.execute(provider="ComfyUI Local")[0]
        self.assertNotIn("prompt_idea", values)
        self.assertNotIn("clip", values)
        output = Generator.execute("A cabin", "Return a prompt.", "None", values, clip=self.local_clip())
        self.assertEqual(output.result[0], "A local cabin.")
        self.assertEqual(json.loads(output.result[1])["provider"], "ComfyUI Local")
        self.assertEqual(tuple(Generator.RETURN_NAMES), ("text", "info"))
        self.assertEqual(output.ui.as_dict(), {"text": ("A local cabin.",)})
        self.post.assert_not_called()
        self.assertTrue(math.isnan(Settings.fingerprint_inputs(force_regenerate=True)))
        with self.assertRaisesRegex(ValueError, "incomplete"):
            Generator.execute("A cabin", "", "None", {"provider": "ComfyUI Local"})

    def test_settings_creation_defaults_and_manual_values(self):
        defaults = {field.id: field.default for field in Settings.define_schema().inputs}
        preset = {"provider": "ComfyUI Local", "thinking": "disabled", "temperature": 0.7, "top_p": 0.95, "top_k": 40, "min_p": 0.05, "repetition_penalty": 1.1}
        self.assertEqual({name: defaults[name] for name in preset}, preset)
        values = {**defaults, "thinking": "enabled", "temperature": 0.4, "top_p": 0.8, "top_k": 12, "min_p": 0.02, "repetition_penalty": 1.0}
        for provider in ("ComfyUI Local", "OpenRouter", "Ollama", "ComfyUI Local"):
            values["provider"] = provider
            self.assertEqual(Settings.execute(**values)[0], {name: value for name, value in values.items() if name != "remember_context"})
        legacy = {field.id: field.default for field in UniversalLLM.define_schema().inputs if field.id in ("provider", "top_p")}
        self.assertEqual(legacy["provider"], "OpenRouter")
        self.assertEqual(legacy["top_p"], -1.0)
        saved = {**defaults, "thinking": "auto", "top_p": -1.0, "top_k": -1, "min_p": -1.0, "repetition_penalty": -1.0}
        self.assertEqual(Settings.execute(**saved)[0], {name: value for name, value in saved.items() if name != "remember_context"})

    def test_generator_context_uses_real_api_output_and_reset(self):
        settings = Settings.execute(provider="OpenRouter", model="test/model", api_key="test-secret", context_window=1024)[0]
        session = "real-api-context-test"
        self.addCleanup(node_module._CONTEXT_MEMORY.discard, session)
        output = Generator.execute("A cabin", "Short prompts.", "None", settings, context_session=session)
        self.assertEqual(output.result[0], "A cinematic cabin.")
        self.assertTrue(json.loads(output.result[1])["context"]["enabled"])
        self.assertEqual(output.ui["context_usage"][0]["limit"], 1024)
        Generator.execute("Add snow", "Short prompts.", "None", settings, context_session=session)
        self.assertEqual([message["role"] for message in self.body()["messages"]], ["system", "user", "assistant", "user"])
        Generator.execute("Fresh", "Short prompts.", "None", settings, context_session=session, context_reset=1)
        self.assertEqual(len(self.body()["messages"]), 2)

    def test_guides_preserve_instructions_and_ref2v_dependency(self):
        self.assertEqual(guides_module.combine_system_prompt("Keep this exact.\n", "None"), "Keep this exact.\n")
        combined = guides_module.combine_system_prompt("Keep original dialogue.", "MiniMax H3 - Ref2V")
        self.assertTrue(combined.startswith("Keep original dialogue."))
        for name in ("MinimaxH3_T2V_FL2V_PromptGuide.md", "MinimaxH3_Ref2V_PromptGuide.md"):
            self.assertIn((NODE_DIR / "prompt_guides" / name).read_text(encoding="utf-8-sig"), combined)
        with self.assertRaisesRegex(ValueError, "Unknown prompt guide"):
            guides_module.combine_system_prompt("", "../../secret")
        clip = self.local_clip()
        Generator.execute("A cabin", "Preserve my wording.", "FLUX.2 Klein 9B", Settings.execute(provider="ComfyUI Local")[0], clip=clip)
        self.assertIn("FLUX.2 Klein 9B", clip.tokenize.call_args.kwargs["system_prompt"])
        self.assertTrue(clip.tokenize.call_args.kwargs["system_prompt"].startswith("Preserve my wording."))

    def test_image_guides_select_mode_from_connected_images(self):
        for guide in ("Qwen Image 2.1", "FLUX.2 Klein 9B", "Krea 2"):
            for references, expected in (((), "T2I"), (("audio1",), "T2I"), (("image1",), "I2I / Ref2I"), (("image2", "image10"), "I2I / Ref2I")):
                with self.subTest(guide=guide, references=references):
                    combined = guides_module.combine_system_prompt("Keep the requested four character-sheet panels.", guide, references)
                    self.assertIn(f"Generation mode for this request: {expected}.", combined)
                    resolved = combined.split("Generation mode for this request: ", 1)[1]
                    if expected == "I2I / Ref2I":
                        self.assertIn("explicitly name the source image", resolved)
                        self.assertIn("not a standalone description", resolved)
                    else:
                        self.assertIn("Do not invent image references", resolved)
                    self.assertIn("Do not output mode labels, guide headings, or rule explanations", resolved)

    def test_video_guides_select_reference_mode_for_any_connected_media(self):
        for guide in ("Seedance 2.0", "Seedance 2.5", "Wan 3.0"):
            for references, expected in (((), "T2V"), (("image1",), "Ref2V"), (("image1", "image2"), "Ref2V"), (("video1",), "Ref2V"), (("audio1",), "Ref2V"), (("image2", "video1", "audio1"), "Ref2V")):
                with self.subTest(guide=guide, references=references):
                    combined = guides_module.combine_system_prompt("Keep the requested action.", guide, references)
                    self.assertIn(f"Generation mode for this request: {expected}.", combined)
                    resolved = combined.split("Generation mode for this request: ", 1)[1]
                    if references:
                        self.assertIn("explicit reference tags", resolved)
                        self.assertIn("only when the user explicitly assigns first/last-frame roles", resolved)
                    else:
                        self.assertIn("Do not invent media references", resolved)
                    self.assertIn("Do not output mode labels, guide headings, or rule explanations", resolved)

    def test_errors_do_not_become_prompt_text(self):
        for status, hint in ((400, "settings"), (401, "API key"), (403, "permissions"), (404, "base_url"), (429, "quota"), (500, "server"), (307, "Redirects")):
            with self.subTest(status=status):
                self.response.status_code = status
                with self.assertRaisesRegex(RuntimeError, hint) as caught:
                    self.generate()
                self.assertNotIn("test-secret", str(caught.exception))
        self.assertEqual(self.response.json.call_count, 7)

    def test_timeout_and_connection_errors(self):
        for error, message in ((requests.exceptions.Timeout("test-secret"), "timed out"), (requests.exceptions.ConnectionError("test-secret"), "Could not reach")):
            with self.subTest(error=type(error).__name__):
                self.post.side_effect = error
                with self.assertRaisesRegex(RuntimeError, message) as caught:
                    self.generate(timeout_seconds=240)
                self.assertNotIn("test-secret", str(caught.exception))
                self.assertEqual(self.post.call_args.kwargs["timeout"], 240)

    def test_invalid_json(self):
        self.response.json.side_effect = requests.exceptions.JSONDecodeError("bad JSON", "<html>", 0)
        with self.assertRaisesRegex(RuntimeError, "invalid JSON"):
            self.generate()

    def test_invalid_response_shape(self):
        for result in ([], {}, {"choices": []}, {"choices": [{"message": {"content": None}}]}, {"error": {"message": "test-secret"}}):
            with self.subTest(result=result):
                self.response.json.return_value = result
                with self.assertRaises(RuntimeError) as caught:
                    self.generate()
                self.assertNotIn("test-secret", str(caught.exception))

    def test_refusal_is_error(self):
        self.response.json.return_value = {"choices": [{"message": {"content": None, "refusal": "No."}}]}
        with self.assertRaisesRegex(RuntimeError, "refused"):
            self.generate()

    def test_cache_invalidation_option(self):
        self.assertEqual(UniversalLLM.IS_CHANGED(force_regenerate=False), "")
        self.assertTrue(math.isnan(UniversalLLM.IS_CHANGED(force_regenerate=True)))
        self.assertEqual(UniversalLLM.fingerprint_inputs(force_regenerate=False), "")
        self.assertTrue(math.isnan(UniversalLLM.fingerprint_inputs(force_regenerate=True)))

    def test_dynamic_media_schema_and_existing_widget_order(self):
        schema = UniversalLLM.GET_SCHEMA()
        expected = ["provider", "model", "prompt_idea", "system_prompt", "api_key", "api_key_env", "base_url", "thinking", "reasoning_effort", "max_output_tokens", "context_window", "temperature", "top_p", "top_k", "min_p", "repetition_penalty", "frequency_penalty", "presence_penalty", "seed", "stop_sequences", "timeout_seconds", "force_regenerate"]
        self.assertEqual([field.id for field in schema.inputs[:len(expected)]], expected)
        self.assertEqual(schema.node_id, "UniversalLLM")
        for name, prefix, maximum, kind in (("images", "image", 10, "IMAGE"), ("videos", "video", 4, "VIDEO"), ("audios", "audio", 6, "AUDIO")):
            with self.subTest(group=name):
                field = next(item for item in schema.inputs if item.id == name)
                self.assertIsInstance(field, io.Autogrow.Input)
                self.assertTrue(field.optional)
                self.assertEqual(field.template.min, 0)
                self.assertEqual(field.template.names, [f"{prefix}{i}" for i in range(1, maximum + 1)])
                self.assertEqual(field.template.input.io_type, kind)

    def test_actual_dynamic_schema_expansion_and_execution(self):
        image = torch.zeros(1, 2, 2, 3)
        live = {"provider": "OpenRouter", "model": "test/model", "prompt_idea": "Describe @image10", "system_prompt": "", "api_key": "test-secret", "images.image10": image}
        inputs, _, data = _io.get_finalized_class_inputs(UniversalLLM.INPUT_TYPES(), live)
        self.assertEqual(inputs["optional"]["images.image10"][0], "IMAGE")
        nested = _io.build_nested_inputs(live, data)
        self.assertIs(nested["images"]["image10"], image)
        self.assertEqual(nested["videos"], {})
        self.assertEqual(nested["audios"], {})
        self.assertEqual(UniversalLLM.EXECUTE_NORMALIZED(**nested).result, ("A cinematic cabin.",))
        self.assertEqual(self.body()["messages"][-1]["content"][1], {"type": "text", "text": "@image10"})

    def test_empty_dynamic_inputs_preserve_text_only_workflow(self):
        live = {"provider": "OpenRouter", "model": "test/model", "prompt_idea": "A cabin", "system_prompt": "Return a prompt.", "api_key": "test-secret"}
        _, _, data = _io.get_finalized_class_inputs(UniversalLLM.INPUT_TYPES(), live)
        nested = _io.build_nested_inputs(live, data)
        for name in ("images", "videos", "audios"):
            self.assertEqual(nested[name], {})
        self.assertEqual(UniversalLLM.EXECUTE_NORMALIZED(**nested).result, ("A cinematic cabin.",))
        self.assertEqual(self.body()["messages"][-1]["content"], "A cabin")

    def test_complete_image_batches_and_numeric_order(self):
        red = torch.tensor([1.0, 0.0, 0.0]).expand(2, 2, 3)
        blue = torch.tensor([0.0, 0.0, 1.0]).expand(2, 2, 3)
        self.generate(prompt_idea="Compare @image1 with @image10", images={"image10": blue.unsqueeze(0), "image1": torch.stack((red, blue)), "image2": None})
        parts = self.body()["messages"][-1]["content"]
        labels = [part["text"] for part in parts if part["type"] == "text"]
        self.assertEqual(labels[1:], ["@image1 image 1/2", "@image1 image 2/2", "@image10"])
        pictures = [part for part in parts if part["type"] == "image_url"]
        self.assertEqual(len(pictures), 3)
        png = base64.b64decode(pictures[0]["image_url"]["url"].split(",", 1)[1])
        with Image.open(BytesIO(png)) as image:
            self.assertEqual(image.size, (2, 2))
            self.assertEqual(image.getpixel((0, 0)), (255, 0, 0))

    def test_tags_validate_both_prompts_before_encoding(self):
        for updates in ({"prompt_idea": "Use @image10"}, {"system_prompt": "Use @audio6"}, {"prompt_idea": "Use @video4"}):
            with self.subTest(updates=updates), patch.object(media_module, "_image_data") as encoder:
                with self.assertRaisesRegex(ValueError, "no connected media"):
                    self.generate(images={"image1": torch.zeros(1, 2, 2, 3)}, **updates)
                encoder.assert_not_called()
        self.post.assert_not_called()

    def test_tag_boundaries_repetition_and_unreferenced_attachments(self):
        self.generate(prompt_idea="Email a@image1.example, escaped @@image2, and literal @image1word; compare @image1 to @image1.", system_prompt="Describe @image1.", images={"image1": torch.zeros(1, 2, 2, 3), "image2": torch.ones(1, 2, 2, 3)})
        pictures = [part for part in self.body()["messages"][-1]["content"] if part["type"] == "image_url"]
        self.assertEqual(len(pictures), 2)

    def test_lm_studio_image_payload(self):
        self.response.json.return_value = {"output": [{"type": "message", "content": "Answer"}]}
        self.generate("LM Studio", images={"image1": torch.zeros(1, 2, 2, 3)})
        body = self.body()
        self.assertEqual(body["input"][0], {"type": "text", "content": "A cabin"})
        self.assertEqual(body["input"][1], {"type": "text", "content": "@image1"})
        self.assertEqual(body["input"][2]["type"], "image")
        self.assertTrue(body["input"][2]["data_url"].startswith("data:image/png;base64,"))
        self.assertEqual(body["system_prompt"], "Return a prompt.")

    def test_ollama_images_have_ordered_labels(self):
        self.response.json.return_value = {"message": {"content": "Answer"}}
        self.generate("Ollama", images={"image2": torch.zeros(1, 2, 2, 3), "image1": torch.ones(1, 2, 2, 3)})
        message = self.body()["messages"][-1]
        self.assertEqual(len(message["images"]), 2)
        self.assertEqual(message["content"], "A cabin\n\nAttachments in order:\n@image1\n@image2")
        self.assertFalse(message["images"][0].startswith("data:"))
        self.assertEqual(self.body()["messages"][0], {"role": "system", "content": "Return a prompt."})

    def test_cerebras_image_payload_has_no_unsupported_detail(self):
        self.generate("Cerebras", images={"image1": torch.zeros(1, 2, 2, 3)})
        picture = self.body()["messages"][-1]["content"][-1]
        self.assertEqual(set(picture["image_url"]), {"url"})
        self.assertTrue(picture["image_url"]["url"].startswith("data:image/png;base64,"))

    def test_complete_audio_batches_are_pcm16_wav(self):
        waveform = torch.stack((torch.zeros(2, 8), torch.full((2, 8), 0.5)))
        self.generate(audios={"audio6": {"waveform": waveform, "sample_rate": 16000}})
        parts = self.body()["messages"][-1]["content"]
        clips = [part["input_audio"] for part in parts if part["type"] == "input_audio"]
        self.assertEqual(len(clips), 2)
        labels = [part["text"] for part in parts if part["type"] == "text"][1:]
        self.assertEqual(labels, ["@audio6 audio 1/2", "@audio6 audio 2/2"])
        for clip in clips:
            self.assertEqual(clip["format"], "wav")
            with wave.open(BytesIO(base64.b64decode(clip["data"])), "rb") as audio:
                self.assertEqual((audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getnframes()), (2, 2, 16000, 8))

    def test_media_encoding_supports_float_storage_dtypes(self):
        for dtype in (torch.float32, torch.float16, torch.bfloat16):
            with self.subTest(dtype=dtype):
                self.generate(images={"image1": torch.zeros(1, 2, 2, 3, dtype=dtype)}, audios={"audio1": {"waveform": torch.zeros(1, 1, 8, dtype=dtype), "sample_rate": 16000}})
                kinds = [part["type"] for part in self.body()["messages"][-1]["content"]]
                self.assertIn("image_url", kinds)
                self.assertIn("input_audio", kinds)

    def test_unsupported_audio_rejected_before_any_encoding(self):
        for provider in ("Cerebras", "LM Studio", "Ollama", "Groq"):
            with self.subTest(provider=provider), patch.object(media_module, "_image_data") as encoder:
                with self.assertRaisesRegex(ValueError, "does not accept audio"):
                    self.generate(provider, images={"image1": torch.zeros(1, 2, 2, 3)}, audios={"audio1": {"waveform": torch.zeros(1, 1, 8), "sample_rate": 16000}})
                encoder.assert_not_called()
        self.post.assert_not_called()

    def test_known_image_only_provider_rejects_native_video(self):
        for provider in ("Cerebras", "LM Studio", "Ollama", "Groq"):
            with self.subTest(provider=provider), self.assertRaisesRegex(ValueError, "does not accept native video"):
                self.generate(provider, videos={"video1": Mock()}, video_mode="native")
        self.post.assert_not_called()

    def make_video(self):
        frames = torch.stack([torch.full((64, 64, 3), value) for value in (0.0, 0.2, 0.4, 0.6, 0.8)])
        audio = {"waveform": torch.zeros(1, 1, 8000), "sample_rate": 8000}
        return InputImpl.VideoFromComponents(Types.VideoComponents(images=frames, frame_rate=Fraction(5), audio=audio))

    def test_native_video_is_mp4_with_soundtrack(self):
        self.generate(videos={"video4": self.make_video()})
        parts = self.body()["messages"][-1]["content"]
        self.assertEqual(parts[-2], {"type": "text", "text": "@video4"})
        url = parts[-1]["video_url"]["url"]
        self.assertTrue(url.startswith("data:video/mp4;base64,"))
        with av.open(BytesIO(base64.b64decode(url.split(",", 1)[1]))) as container:
            self.assertEqual(container.streams.video[0].codec_context.name, "h264")
            self.assertEqual(len(container.streams.audio), 1)
            self.assertEqual(len(list(container.decode(video=0))), 5)

    def test_video_frames_honor_trim_and_crop(self):
        buffer = BytesIO()
        self.make_video().save_to(buffer, format=Types.VideoContainer.MP4, codec=Types.VideoCodec.H264)
        video = InputImpl.VideoFromFile(buffer).as_trimmed(0.2, 0.6).as_cropped(0, 0, 32, 32)
        self.response.json.return_value = {"message": {"content": "Answer"}}
        self.generate("Ollama", videos={"video1": video}, video_frame_count=3)
        message = self.body()["messages"][-1]
        self.assertEqual(len(message["images"]), 3)
        self.assertIn("@video1 frame at 0.000s", message["content"])
        self.assertIn("@video1 frame at 0.400s", message["content"])
        for data in message["images"]:
            with Image.open(BytesIO(base64.b64decode(data))) as image:
                self.assertEqual(image.size, (32, 32))
                self.assertGreater(image.getpixel((0, 0))[0], 30)

    def test_explicit_frame_mode_on_hosted_provider(self):
        self.generate(videos={"video1": self.make_video()}, video_mode="frames", video_frame_count=1)
        parts = self.body()["messages"][-1]["content"]
        self.assertEqual(sum(part["type"] == "image_url" for part in parts), 1)
        self.assertNotIn("video_url", [part["type"] for part in parts])

    def test_video_sampling_uses_decodable_timestamps(self):
        buffer = BytesIO()
        with av.open(buffer, "w", format="mp4") as output:
            stream = output.add_stream("libx264", rate=50)
            stream.options = {"bf": "0"}
            stream.width = stream.height = 32
            stream.pix_fmt = "yuv420p"
            for index in (0, 1, 2, 6, 7):
                frame = av.VideoFrame.from_ndarray(torch.full((32, 32, 3), index * 30, dtype=torch.uint8).numpy(), format="rgb24")
                frame.pts = index
                frame.time_base = Fraction(1, 50)
                for packet in stream.encode(frame):
                    packet.duration = 3
                    output.mux(packet)
            for packet in stream.encode():
                packet.duration = 3
                output.mux(packet)
        video = InputImpl.VideoFromFile(buffer)
        for provider in ("OpenRouter", "ComfyUI Local"):
            for count in (1, 2, 12):
                with self.subTest(provider=provider, count=count):
                    clip = self.local_clip("gemma4")
                    self.generate(provider, clip=clip, videos={"video1": video}, video_mode="frames", video_frame_count=count)
                    if provider == "ComfyUI Local":
                        prompt = clip.tokenize.call_args.args[0]
                        self.assertEqual(clip.tokenize.call_args.kwargs["image"].shape, (count, 32, 32, 3))
                    else:
                        parts = self.body()["messages"][-1]["content"]
                        prompt = "\n".join(part["text"] for part in parts if part["type"] == "text")
                        self.assertEqual(sum(part["type"] == "image_url" for part in parts), count)
                    self.assertIn("frame at 0.000s", prompt)
                    if count > 1:
                        self.assertIn("frame at 0.140s", prompt)

    def test_video_sampling_materializes_source_at_most_once(self):
        video = self.make_video()
        self.response.json.return_value = {"message": {"content": "Answer"}}
        with patch.object(video, "save_to", wraps=video.save_to) as serialize:
            self.generate("Ollama", videos={"video1": video}, video_frame_count=12)
        self.assertEqual(len(self.body()["messages"][-1]["images"]), 12)
        self.assertLessEqual(serialize.call_count, 1)

    def test_automatic_frame_count_and_explicit_provider_override(self):
        for provider, count, expected in (("Cerebras", 0, 2), ("Cerebras", 12, 12), ("OpenRouter", 0, 12), ("Groq", 0, 2), ("Groq", 12, 12)):
            with self.subTest(provider=provider, count=count):
                self.generate(provider, videos={"video1": self.make_video()}, video_mode="frames", video_frame_count=count)
                parts = self.body()["messages"][-1]["content"]
                self.assertEqual(sum(part["type"] == "image_url" for part in parts), expected)

    def local_clip(self, namespace="qwen3_4b", answer="A local cabin."):
        clip = Mock(spec=["tokenize", "generate", "decode", "is_dynamic", "clone"])
        clip.is_dynamic.return_value = False
        def tokenize(prompt, **kwargs):
            tokens = [(1, 1.0)]
            image = kwargs.get("image")
            if image is not None:
                tokens.extend(({"type": "image", "data": frame.unsqueeze(0)}, 1.0) for frame in image)
            if kwargs.get("audio") is not None:
                tokens.append(({"type": "audio", "data": torch.zeros(1, 1, 128), "mask": torch.ones(1, 1, dtype=torch.bool)}, 1.0))
            return {namespace: [tokens]}
        clip.tokenize.side_effect = tokenize
        clip.generate.return_value = [2, 3]
        clip.decode.return_value = answer
        return clip

    def test_local_provider_schema_and_optional_clip(self):
        schema = UniversalLLM.GET_SCHEMA()
        self.assertIn("ComfyUI Local", schema.inputs[0].options)
        field = next(item for item in schema.inputs if item.id == "clip")
        self.assertEqual(field.io_type, "CLIP")
        self.assertTrue(field.optional)

    def test_local_generation_uses_clip_without_api_or_model_name(self):
        clip = self.local_clip()
        self.assertEqual(self.generate("ComfyUI Local", model="", clip=clip, base_url="not a URL", api_key="unused"), ("A local cabin.",))
        self.post.assert_not_called()
        args = clip.tokenize.call_args
        self.assertEqual(args.args, ("A cabin",))
        self.assertEqual(args.kwargs["system_prompt"], "Return a prompt.")
        self.assertFalse(args.kwargs["skip_template"])
        self.assertEqual(clip.decode.call_args.args, ([2, 3],))

    def test_local_native_sampling_defaults_and_greedy_mode(self):
        clip = self.local_clip()
        self.generate("ComfyUI Local", clip=clip, temperature=-1)
        settings = clip.generate.call_args.kwargs
        self.assertEqual((settings["temperature"], settings["top_k"], settings["top_p"], settings["min_p"], settings["repetition_penalty"]), (1.0, 50, 1.0, 0.0, 1.0))
        self.assertIsInstance(settings["seed"], int)
        self.generate("ComfyUI Local", clip=clip, temperature=0, max_output_tokens=77, top_k=4, top_p=0.8, min_p=0.2, repetition_penalty=1.1, presence_penalty=-0.5, seed=42, thinking="enabled")
        settings = clip.generate.call_args.kwargs
        self.assertFalse(settings["do_sample"])
        self.assertEqual(settings["max_length"], 77)
        self.assertEqual(settings["presence_penalty"], -0.5)
        self.assertEqual(settings["seed"], 42)
        self.assertTrue(clip.tokenize.call_args.kwargs["thinking"])

    def test_local_default_seed_meets_native_torch_generator_contract(self):
        clip = self.local_clip()
        def generate(tokens, **settings):
            torch.Generator(device="cpu").manual_seed(settings["seed"])
            return [2, 3]
        clip.generate.side_effect = generate
        self.assertEqual(self.generate("ComfyUI Local", clip=clip), ("A local cabin.",))

    def test_local_dynamic_qwen_clone_generates_with_its_managed_model(self):
        clip = self.local_clip()
        clip.is_dynamic.return_value = True
        original_model = object()
        managed_model = object()
        clip.cond_stage_model = original_model
        cloned = self.local_clip()
        cloned.cond_stage_model = original_model
        cloned.patcher = SimpleNamespace(model=managed_model)
        clip.clone.return_value = cloned
        def generate(tokens, **settings):
            self.assertIs(cloned.cond_stage_model, managed_model)
            return [2, 3]
        cloned.generate.side_effect = generate
        self.assertEqual(self.generate("ComfyUI Local", clip=clip), ("A local cabin.",))
        clip.clone.assert_called_once_with(disable_dynamic=True)
        clip.generate.assert_not_called()
        self.assertIs(clip.cond_stage_model, original_model)
        self.assertTrue(clip.is_dynamic())

    def test_local_qwen_thinking_templates_preserve_system_prompt(self):
        class BaseTokenizer:
            def tokenize_with_weights(self, text, **kwargs):
                self.formatted_text = text
                return {self.token_name: [[(1, 1.0)]]}

        for filename, class_name, token_name, closed_default in (
            ("z_image.py", "ZImageTokenizer", "qwen3_4b", False),
            ("flux.py", "KleinTokenizer", "qwen3_8b", True),
        ):
            source = ast.parse((COMFY_DIR / "comfy/text_encoders" / filename).read_text(encoding="utf-8"))
            native_class = next(item for item in source.body if isinstance(item, ast.ClassDef) and item.name == class_name)
            method = next(item for item in native_class.body if isinstance(item, ast.FunctionDef) and item.name == "tokenize_with_weights")
            probe_class = ast.ClassDef(name="TokenizerProbe", bases=[ast.Name(id="BaseTokenizer", ctx=ast.Load())], keywords=[], body=[method], decorator_list=[])
            namespace = {"BaseTokenizer": BaseTokenizer}
            exec(compile(ast.fix_missing_locations(ast.Module(body=[probe_class], type_ignores=[])), "native_qwen_template", "exec"), namespace)
            tokenizer = namespace["TokenizerProbe"]()
            tokenizer.token_name = token_name
            tokenizer.llama_template = "<|im_start|>user\n{}<|im_end|>\n<|im_start|>assistant\n" + ("<think>\n\n</think>\n\n" if closed_default else "")
            clip = self.local_clip(token_name)
            clip.tokenize.side_effect = tokenizer.tokenize_with_weights
            for thinking, closed in (("enabled", False), ("disabled", True), ("auto", closed_default)):
                with self.subTest(tokenizer=class_name, thinking=thinking):
                    self.generate("ComfyUI Local", clip=clip, thinking=thinking)
                    self.assertIn("<|im_start|>system\nReturn a prompt.<|im_end|>", tokenizer.formatted_text)
                    self.assertEqual("<think>\n\n</think>\n\n" in tokenizer.formatted_text, closed)

    def test_local_missing_clip_and_unsupported_encoders(self):
        with self.assertRaisesRegex(ValueError, "Connect.*Load CLIP"):
            self.generate("ComfyUI Local")
        for namespace, message in (("l", "embeddings"), ("t5xxl", "embeddings"), ("qwen3vl_32b", "truncated")):
            with self.subTest(namespace=namespace):
                clip = self.local_clip(namespace)
                with self.assertRaisesRegex(ValueError, message):
                    self.generate("ComfyUI Local", clip=clip)
                clip.generate.assert_not_called()

    def test_local_generation_attribute_errors_keep_unrelated_failures(self):
        clip = self.local_clip()
        clip.generate.side_effect = AttributeError("no generate", name="generate")
        with self.assertRaisesRegex(ValueError, "cannot generate"):
            self.generate("ComfyUI Local", clip=clip)
        clip.generate.side_effect = AttributeError("unrelated", name="broken_parameter")
        with self.assertRaisesRegex(AttributeError, "unrelated"):
            self.generate("ComfyUI Local", clip=clip)

    def test_local_reasoning_and_literal_stop_sequences(self):
        for answer, expected in (("<think>private</think> A cabin END extra", "A cabin"), ("A cabin STOP other END", "A cabin"), ("A <think>literal tag", "A <think>literal tag")):
            with self.subTest(answer=answer):
                clip = self.local_clip(answer=answer)
                self.assertEqual(self.generate("ComfyUI Local", clip=clip, stop_sequences="END\nSTOP"), (expected,))

    def test_compact_stop_field_and_json_literal_sequences(self):
        field = next(field for field in node_module.GOATTextGeneratorSettings.define_schema().inputs if field.id == "stop_sequences")
        self.assertFalse(field.multiline)
        stops = ["\nUser:", "one,two", "[END]"]
        encoded = json.dumps(stops)
        self.generate(stop_sequences=encoded)
        self.assertEqual(self.body()["stop"], stops)
        clip = self.local_clip(answer="A cabin\nUser: another turn")
        self.assertEqual(self.generate("ComfyUI Local", clip=clip, stop_sequences=encoded), ("A cabin",))
        self.assertEqual(node_module.parse_stop_sequences("END\n\n stop "), ["END", " stop "])
        self.assertEqual(node_module.parse_stop_sequences("[END]"), ["[END]"])
        self.assertEqual(node_module.parse_stop_sequences("[]"), [])
        clip = self.local_clip(answer="A cabin[END] extra")
        self.assertEqual(self.generate("ComfyUI Local", clip=clip, stop_sequences="[END]"), ("A cabin",))

    def test_stop_json_rejects_non_string_elements_before_generation(self):
        for value in ('["END", 1]', '[null]', '[true]', '[{}]'):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "only strings"):
                self.generate(stop_sequences=value)
            clip = self.local_clip()
            with self.subTest(local=value), self.assertRaisesRegex(ValueError, "only strings"):
                self.generate("ComfyUI Local", clip=clip, stop_sequences=value)
            clip.generate.assert_not_called()
        self.post.assert_not_called()

    def test_generator_supplies_only_connected_reference_names_to_guide(self):
        settings = node_module.GOATTextGeneratorSettings.execute(provider="ComfyUI Local")[0]
        with patch.object(node_module, "combine_system_prompt", return_value="Combined system") as combine, patch.object(UniversalLLM, "generate", return_value=("A cabin",)) as generate:
            result = node_module.GOATUniversalTextGenerator.execute(
                "A cabin", "System", "None", settings,
                images={"image1": object(), "image2": None},
                videos={"video1": object()}, audios={"audio1": object()},
            )
        combine.assert_called_once_with("System", "None", reference_names=("image1", "video1", "audio1"), json_output=False)
        self.assertEqual(generate.call_args.kwargs["system_prompt"], "Combined system")
        self.assertEqual(result[0], "A cabin")

    def test_fibo_guide_uses_the_settings_output_format(self):
        for enabled in (False, True):
            for images in (None, {"image2": object(), "image10": object()}):
                with self.subTest(json_output=enabled, images=bool(images)):
                    settings = Settings.execute(provider="ComfyUI Local", json_output=enabled)[0]
                    answer = '{"short_description":"A cabin"}' if enabled else "A cabin"
                    with patch.object(UniversalLLM, "generate", return_value=(answer,)) as generate:
                        result = Generator.execute("A cabin", "Preserve details.", "Fibo 2", settings, images=images)
                    system = generate.call_args.kwargs["system_prompt"]
                    self.assertEqual(result[0], answer)
                    if enabled:
                        self.assertIn('"short_description"', system)
                        self.assertIn("VGL JSON object", system)
                    else:
                        self.assertIn("Return only plain-language", system)
                        self.assertNotIn('"short_description"', system)
                    if images:
                        self.assertIn("@image10 corresponds to <image_2>", system)

    def test_local_empty_and_unfinished_reasoning_cannot_complete_blank(self):
        for answer, message in (("<think>unfinished", "stopped during reasoning"), ("<think>done</think>", "no answer text"), (" \n", "no answer text"), ("STOP later", "no answer text")):
            with self.subTest(answer=answer), self.assertRaisesRegex(RuntimeError, message):
                self.generate("ComfyUI Local", clip=self.local_clip(answer=answer), stop_sequences="STOP")

    def test_local_media_preserves_different_sizes_and_full_batches(self):
        clip = self.local_clip("gemma4")
        self.generate("ComfyUI Local", clip=clip, prompt_idea="Compare @image1 with @image10", images={"image10": torch.ones(1, 3, 4, 3), "image1": torch.zeros(2, 2, 2, 3)})
        call = clip.tokenize.call_args
        self.assertEqual(call.kwargs["image"].shape, (3, 3, 4, 3))
        self.assertEqual(call.kwargs["image"][-1].sum().item(), 36)
        self.assertIn("@image1 image 2/2\n@image10", call.args[0])

    def test_local_media_tags_and_ignored_modalities_fail_before_generation(self):
        clip = self.local_clip()
        with self.assertRaisesRegex(ValueError, "no connected media"):
            self.generate("ComfyUI Local", clip=clip, system_prompt="Describe @audio1")
        clip.tokenize.side_effect = None
        clip.tokenize.return_value = {"qwen3_4b": [[(1, 1.0)]]}
        with self.assertRaisesRegex(ValueError, "every image"):
            self.generate("ComfyUI Local", clip=clip, images={"image1": torch.zeros(1, 2, 2, 3)})
        with self.assertRaisesRegex(ValueError, "does not accept audio"):
            self.generate("ComfyUI Local", clip=clip, audios={"audio1": {"waveform": torch.zeros(1, 1, 10), "sample_rate": 16000}})
        clip.generate.assert_not_called()

    def test_local_audio_batch_concatenation_and_segment_labels(self):
        clip = self.local_clip("gemma4")
        self.generate("ComfyUI Local", clip=clip, audios={"audio2": {"waveform": torch.ones(1, 1, 160), "sample_rate": 16000}, "audio1": {"waveform": torch.zeros(2, 2, 80), "sample_rate": 8000}})
        args = clip.tokenize.call_args
        audio = args.kwargs["audio"]
        self.assertEqual(audio["waveform"].shape, (1, 1, 480))
        self.assertEqual(audio["sample_rate"], 16000)
        self.assertIn("@audio1 audio 1/2 at 0.000s to 0.010s", args.args[0])
        self.assertIn("@audio2 at 0.020s to 0.030s", args.args[0])

    def test_local_audio_is_ready_for_native_numpy_features(self):
        clip = self.local_clip("gemma4")
        waveform = torch.zeros(1, 2, 80, requires_grad=True)
        self.generate("ComfyUI Local", clip=clip, audios={"audio1": {"waveform": waveform, "sample_rate": 8000}})
        audio = clip.tokenize.call_args.kwargs["audio"]["waveform"]
        self.assertEqual(audio.device.type, "cpu")
        self.assertEqual(audio.dtype, torch.float32)
        self.assertEqual(audio.numpy().shape, (1, 1, 160))

    def test_local_native_unified_audio_truncation_is_reported(self):
        source = ast.parse((COMFY_DIR / "comfy/text_encoders/gemma4.py").read_text(encoding="utf-8"))
        native_class = next(item for item in source.body if isinstance(item, ast.ClassDef) and item.name == "Gemma4UnifiedSDTokenizer")
        method = next(item for item in native_class.body if isinstance(item, ast.FunctionDef) and item.name == "_extract_audio_features")
        namespace = {"torch": torch}
        exec(compile(ast.Module(body=[method], type_ignores=[]), "native_gemma_audio", "exec"), namespace)
        extractor = Mock()
        extractor._resample_16k.side_effect = lambda waveform, sample_rate: waveform.squeeze()
        clip = self.local_clip("gemma4")
        def tokenize(prompt, **kwargs):
            audio = kwargs["audio"]
            features, mask = namespace["_extract_audio_features"](extractor, audio["waveform"], audio["sample_rate"])
            return {"gemma4": [[({"type": "audio", "data": features.unsqueeze(0), "mask": mask.unsqueeze(0)}, 1.0)]]}
        clip.tokenize.side_effect = tokenize
        with self.assertRaisesRegex(ValueError, "30.*seconds"):
            self.generate("ComfyUI Local", clip=clip, audios={"audio1": {"waveform": torch.zeros(1, 1, 480000), "sample_rate": 16000}, "audio2": {"waveform": torch.ones(1, 1, 160000), "sample_rate": 16000}})
        clip.generate.assert_not_called()
        self.assertEqual(self.generate("ComfyUI Local", clip=clip, audios={"audio1": {"waveform": torch.zeros(1, 1, 480000), "sample_rate": 16000}}), ("A local cabin.",))

    def test_local_image_batch_preserves_dtype_promotion(self):
        clip = self.local_clip("gemma4")
        self.generate("ComfyUI Local", clip=clip, images={"image1": torch.zeros(1, 2, 2, 3, dtype=torch.float16), "image2": torch.full((1, 2, 2, 3), 0.123456, dtype=torch.float32)})
        image = clip.tokenize.call_args.kwargs["image"]
        self.assertEqual(image.dtype, torch.float32)
        self.assertEqual(image[1, 0, 0, 0], torch.tensor(0.123456))

    def test_local_video_sampling_preserves_trim_crop_and_labels(self):
        buffer = BytesIO()
        self.make_video().save_to(buffer, format=Types.VideoContainer.MP4, codec=Types.VideoCodec.H264)
        video = InputImpl.VideoFromFile(buffer).as_trimmed(0.2, 0.6).as_cropped(0, 0, 32, 32)
        clip = self.local_clip("gemma4")
        self.generate("ComfyUI Local", clip=clip, videos={"video1": video}, video_frame_count=3)
        call = clip.tokenize.call_args
        self.assertEqual(call.kwargs["image"].shape, (3, 32, 32, 3))
        self.assertIn("@video1 frame at 0.400s (visual frames only)", call.args[0])
        with self.assertRaisesRegex(ValueError, "visual frames"):
            self.generate("ComfyUI Local", clip=clip, videos={"video1": video}, video_mode="native")

    def test_local_video_sampling_materializes_once(self):
        video = self.make_video()
        clip = self.local_clip("gemma4")
        with patch.object(video, "save_to", wraps=video.save_to) as serialize:
            self.generate("ComfyUI Local", clip=clip, videos={"video1": video})
        self.assertEqual(clip.tokenize.call_args.kwargs["image"].shape[0], 12)
        self.assertLessEqual(serialize.call_count, 1)

    def test_custom_server_receives_all_media_types(self):
        self.generate("OpenAI-compatible", base_url="http://localhost:8080/v1", images={"image1": torch.zeros(1, 2, 2, 3)}, videos={"video1": self.make_video()}, audios={"audio1": {"waveform": torch.zeros(1, 1, 8), "sample_rate": 16000}})
        kinds = {part["type"] for part in self.body()["messages"][-1]["content"]}
        self.assertEqual(kinds, {"text", "image_url", "video_url", "input_audio"})

    def gguf_loader_namespace(self, names, **values):
        source = ast.parse((COMFY_DIR / "custom_nodes/ComfyUI-GGUF/loader.py").read_text(encoding="utf-8"))
        body = []
        found = set()
        for item in source.body:
            name = item.name if isinstance(item, (ast.FunctionDef, ast.ClassDef)) else None
            if isinstance(item, ast.Assign) and isinstance(item.targets[0], ast.Name):
                name = item.targets[0].id
            elif isinstance(item, ast.Assign) and isinstance(item.targets[0], ast.Subscript) and isinstance(item.targets[0].value, ast.Name):
                name = item.targets[0].value.id
            if name in names:
                body.append(item)
                found.add(name)
            elif isinstance(item, ast.Expr) and isinstance(item.value, ast.Call) and isinstance(item.value.func, ast.Attribute) and isinstance(item.value.func.value, ast.Name) and item.value.func.value.id in names:
                body.append(item)
        self.assertEqual(found, set(names))
        namespace = {"torch": torch, "json": json, **values}
        exec(compile(ast.Module(body=body, type_ignores=[]), "gguf_gemma4_contract", "exec"), namespace)
        return namespace

    def test_gemma4_gguf_tokenizer_metadata_preserves_ids_and_merges(self):
        metadata = {
            "tokenizer.ggml.tokens": ["<pad>", "<eos>", "<bos>", "<unk>", "a", "b", "ab", "▁", "<|turn>"],
            "tokenizer.ggml.token_type": [3, 3, 3, 2, 1, 1, 1, 1, 3],
            "tokenizer.ggml.merges": ["a b"],
            "tokenizer.ggml.model": "gemma4",
        }
        namespace = self.gguf_loader_namespace(["gemma4_tokenizer_json"], get_list_field=lambda reader, name, kind: reader.get(name), get_field=lambda reader, name, kind: reader.get(name))
        data = json.loads(namespace["gemma4_tokenizer_json"](metadata))
        self.assertEqual(data["model"]["vocab"], {token: index for index, token in enumerate(metadata["tokenizer.ggml.tokens"])})
        self.assertEqual(data["model"]["merges"], [["a", "b"]])
        self.assertEqual(next(item["id"] for item in data["added_tokens"] if item["content"] == "<|turn>"), 8)
        self.assertTrue(data["model"]["byte_fallback"])
        self.assertTrue(data["model"]["fuse_unk"])

    def test_gemma4_gguf_text_model_does_not_allocate_media_weights(self):
        transformer = Mock(return_value=SimpleNamespace(config=SimpleNamespace()))
        namespace = self.gguf_loader_namespace(["Gemma4GGUFTextModel"], gemma4=SimpleNamespace(Gemma4_12B=object, Gemma4Transformer=transformer))
        model = namespace["Gemma4GGUFTextModel"]()
        config = SimpleNamespace(num_hidden_layers=48)
        model._init_model(config, torch.float16, "cpu", "gguf_ops")
        self.assertEqual(model.num_layers, 48)
        self.assertEqual(model.dtype, torch.float16)
        self.assertFalse(hasattr(model, "vision_model"))
        self.assertFalse(hasattr(model, "audio_projector"))
        with self.assertRaisesRegex(ValueError, "text weights only"):
            model.preprocess_embed({"type": "image", "data": torch.empty(1)}, "cpu")

    def test_gemma4_gguf_tokenizer_rejects_media_before_native_processing(self):
        class NativeTokenizer:
            def tokenize_with_weights(self, text, return_word_ids=False, **kwargs):
                return text, kwargs

        namespace = self.gguf_loader_namespace(["Gemma4GGUFTextTokenizer"], gemma4=SimpleNamespace(Gemma4UnifiedSDTokenizer=NativeTokenizer))
        tokenizer = namespace["Gemma4GGUFTextTokenizer"]()
        for media in ({"image": torch.empty(1)}, {"audio": {}}, {"video": torch.empty(1)}):
            with self.subTest(media=list(media)), self.assertRaisesRegex(ValueError, "text weights only"):
                tokenizer.tokenize_with_weights("Describe this", **media)
        text, options = tokenizer.tokenize_with_weights("Hello", thinking=True, system_prompt="Be concise.")
        self.assertEqual(text, "Hello")
        self.assertTrue(options["thinking"])
        self.assertEqual(options["system_prompt"], "Be concise.")

    def test_gemma4_gguf_mapping_keeps_norms_and_layer_scalars(self):
        norm = torch.tensor([1.25])
        scalar = torch.tensor([0.75])
        raw = {"token_embd.weight": SimpleNamespace(shape=(262144, 3840)), "output_norm.weight": norm}
        for index in range(48):
            names = ["attn_norm", "post_attention_norm", "ffn_norm", "post_ffw_norm", "attn_q_norm", "attn_k_norm", "attn_q", "attn_k", "attn_output", "ffn_up", "ffn_gate", "ffn_down", "layer_output_scale"]
            if index % 6 != 5:
                names.append("attn_v")
            raw.update({f"blk.{index}.{name}.weight": scalar if name == "layer_output_scale" else norm for name in names})
        load = Mock(return_value=(raw.copy(), {"arch_str": "gemma4", "metadata": {}}))
        fields = {name: SimpleNamespace(types=[gguf.GGUFValueType.UINT32], parts=[torch.tensor(value)], data=[0]) for name, value in (("gemma4.block_count", 48), ("gemma4.embedding_length", 3840))}
        reader = SimpleNamespace(get_field=fields.get)
        namespace = self.gguf_loader_namespace(
            ["LLAMA_SD_MAP", "GEMMA3_SD_MAP", "GEMMA4_SD_MAP", "sd_map_replace", "get_field", "gguf_clip_loader"],
            gguf_sd_loader=load, gguf=SimpleNamespace(GGUFReader=Mock(return_value=reader), GGUFValueType=gguf.GGUFValueType), gemma4_tokenizer_json=Mock(return_value=b"{}"),
            dequantize_tensor=lambda value, dtype: value, logging=Mock(),
        )
        mapped = namespace["gguf_clip_loader"]("test.gguf")
        self.assertIs(mapped["model.norm.weight"], norm)
        self.assertIs(mapped["model.layers.0.self_attn.q_norm.weight"], norm)
        self.assertIs(mapped["model.layers.47.layer_scalar"], scalar)
        self.assertTrue(mapped["gemma4_gguf_text_only"])
        incomplete = raw.copy()
        del incomplete["output_norm.weight"]
        load.return_value = incomplete, {"arch_str": "gemma4", "metadata": {}}
        with self.assertRaisesRegex(ValueError, "missing.*output_norm"):
            namespace["gguf_clip_loader"]("incomplete.gguf")

    def test_gemma4_gguf_public_clip_construction_and_generic_loader(self):
        source = ast.parse((COMFY_DIR / "custom_nodes/ComfyUI-GGUF/nodes.py").read_text(encoding="utf-8"))
        native_class = next(item for item in source.body if isinstance(item, ast.ClassDef) and item.name == "CLIPLoaderGGUF")
        method = next(item for item in native_class.body if isinstance(item, ast.FunctionDef) and item.name == "load_patcher")
        loaded = SimpleNamespace(patcher="initial_patcher")
        public_clip = Mock(return_value=loaded)
        generic_loader = Mock(return_value=loaded)
        tokenizer_json = torch.tensor([123, 125], dtype=torch.uint8)
        text_model, text_tokenizer = object(), object()
        namespace = {
            "SimpleNamespace": SimpleNamespace, "GGMLOps": object(),
            "Gemma4GGUFTextModel": text_model, "Gemma4GGUFTextTokenizer": text_tokenizer,
            "GGUFModelPatcher": SimpleNamespace(clone=Mock(return_value="gguf_patcher")),
            "folder_paths": SimpleNamespace(get_folder_paths=Mock(return_value=[])),
            "comfy": SimpleNamespace(
                sd=SimpleNamespace(CLIP=public_clip, load_text_encoder_state_dicts=generic_loader),
                utils=SimpleNamespace(calculate_parameters=Mock(return_value=12)),
                model_management=SimpleNamespace(text_encoder_offload_device=Mock(return_value="cpu")),
                text_encoders=SimpleNamespace(gemma4=SimpleNamespace(gemma4_te=Mock(return_value="native_gemma_model"), Gemma4UnifiedTokenizer="native_gemma_tokenizer")),
            ),
        }
        exec(compile(ast.Module(body=[method], type_ignores=[]), "gguf_clip_construction", "exec"), namespace)
        weights = {"gemma4_gguf_text_only": True, "tokenizer_json": tokenizer_json, "model.norm.weight": torch.ones(1)}
        namespace["load_patcher"](None, ["test.gguf"], "stable_diffusion", [weights])
        args = public_clip.call_args
        self.assertEqual(args.kwargs["state_dict"], [weights])
        self.assertNotIn("tokenizer_json", weights)
        self.assertNotIn("gemma4_gguf_text_only", weights)
        self.assertIs(args.kwargs["tokenizer_data"]["tokenizer_json"], tokenizer_json)
        self.assertIs(args.kwargs["tokenizer_data"]["gemma4_tokenizer_class"], text_tokenizer)
        self.assertTrue(args.kwargs["disable_dynamic"])
        self.assertEqual(loaded.patcher, "gguf_patcher")
        generic_loader.assert_not_called()
        namespace["load_patcher"](None, ["other.gguf"], "stable_diffusion", [{"model.norm.weight": torch.ones(1)}])
        generic_loader.assert_called_once()
        with self.assertRaisesRegex(ValueError, "alone"):
            namespace["load_patcher"](None, ["test.gguf", "other.gguf"], "stable_diffusion", [{"gemma4_gguf_text_only": True}, {}])

    def test_media_socket_limits_and_invalid_batch_shapes(self):
        for inputs in ({"images": {"image11": torch.zeros(1, 2, 2, 3)}}, {"videos": {"video5": Mock()}}, {"audios": {"audio7": {}}}, {"images": {"image1": torch.zeros(0, 2, 2, 3)}}, {"audios": {"audio1": {"waveform": torch.zeros(0, 1, 8)}}}):
            with self.subTest(inputs=list(inputs)), self.assertRaises(ValueError):
                self.generate(**inputs)
        self.post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
