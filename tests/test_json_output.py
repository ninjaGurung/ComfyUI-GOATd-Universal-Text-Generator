import ast
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import requests

import test_context as context_contracts


namespace = dict(context_contracts.namespace, json=json)
definitions = context_contracts.definitions + [node for node in context_contracts.SOURCE.body if isinstance(node, ast.FunctionDef) and node.name in {"_validate_json", "_reject_json_constant"}]
exec(compile(ast.Module(body=definitions, type_ignores=[]), "llm_node.py", "exec"), namespace)
node = next(node for node in context_contracts.SOURCE.body if isinstance(node, ast.ClassDef) and node.name == "UniversalLLM")
generate = next(node for node in node.body if isinstance(node, ast.FunctionDef) and node.name == "generate")
generate.decorator_list = []
exec(compile(ast.Module(body=[generate], type_ignores=[]), "llm_node.py", "exec"), namespace)


class JsonOutputTests(unittest.TestCase):
    def setUp(self):
        self.response = Mock(status_code=200)
        self.response.__enter__ = Mock(return_value=self.response)
        self.response.__exit__ = Mock(return_value=False)
        self.post = Mock(return_value=self.response)
        namespace["requests"] = SimpleNamespace(post=self.post, exceptions=requests.exceptions)
        namespace["add_media"] = Mock()
        namespace["generate_local"] = Mock(return_value=' {"prompt": "local"} ')

    def generate(self, provider, text=' {"prompt": "answer"} ', enabled=True):
        if provider == "LM Studio":
            self.response.json.return_value = {"output": [{"type": "message", "content": text}]}
        elif provider == "Ollama":
            self.response.json.return_value = {"message": {"content": text}}
        else:
            self.response.json.return_value = {"choices": [{"message": {"content": text}}]}
        return namespace["generate"](None, provider=provider, model="test", prompt_idea="idea", system_prompt="Return plain text only.", api_key="secret", json_output=enabled)

    def test_compatible_providers_request_json_object_and_preserve_text(self):
        for provider in ("OpenAI-compatible", "OpenRouter", "Cerebras", "Groq", "OpenCode"):
            with self.subTest(provider=provider):
                self.assertEqual(self.generate(provider), (' {"prompt": "answer"} ',))
                body = self.post.call_args.kwargs["json"]
                self.assertEqual(body["response_format"], {"type": "json_object"})
                system = body["messages"][0]["content"]
                self.assertIn("overrides conflicting format instructions above", system)
                self.assertIn("valid JSON object", system)

    def test_ollama_uses_native_format(self):
        self.generate("Ollama")
        body = self.post.call_args.kwargs["json"]
        self.assertEqual(body["format"], "json")
        self.assertNotIn("response_format", body)

    def test_lm_studio_native_uses_instructions_without_changing_endpoint(self):
        self.generate("LM Studio")
        body = self.post.call_args.kwargs["json"]
        self.assertNotIn("response_format", body)
        self.assertNotIn("format", body)
        self.assertIn("valid JSON object", body["system_prompt"])
        self.assertEqual(self.post.call_args.args[0], "http://127.0.0.1:1234/api/v1/chat")

    def test_local_instructions_and_validation(self):
        self.assertEqual(self.generate("ComfyUI Local"), (' {"prompt": "local"} ',))
        self.assertIn("valid JSON object", namespace["generate_local"].call_args.args[2])
        namespace["generate_local"].return_value = "plain text"
        with self.assertRaisesRegex(RuntimeError, "invalid JSON"):
            self.generate("ComfyUI Local")

    def test_invalid_and_nonstandard_json_are_rejected(self):
        for text in ("plain text", '```json\n{"prompt":"answer"}\n```', '{"prompt":', '{"value":NaN}', '{"value":Infinity}', '"plain string"', '123', 'null', '[{"prompt":"answer"}]'):
            with self.subTest(text=text), self.assertRaisesRegex(RuntimeError, "invalid JSON"):
                self.generate("OpenAI-compatible", text=text)

    def test_requested_object_structure_is_preserved(self):
        text = '{"scene":{"camera":"close-up"},"subjects":[{"name":"cat"}]}'
        self.assertEqual(self.generate("OpenAI-compatible", text=text), (text,))

    def test_disabled_preserves_plain_text_and_omits_json_controls(self):
        for provider in ("OpenAI-compatible", "OpenRouter", "Cerebras", "Groq", "OpenCode", "Ollama", "LM Studio"):
            with self.subTest(provider=provider):
                self.assertEqual(self.generate(provider, text="plain text", enabled=False), ("plain text",))
                body = self.post.call_args.kwargs["json"]
                self.assertNotIn("format", body)
                self.assertNotIn("response_format", body)
                system = body["system_prompt"] if provider == "LM Studio" else body["messages"][0]["content"]
                self.assertEqual(system, "Return plain text only.")


if __name__ == "__main__":
    unittest.main()
