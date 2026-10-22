import ast
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlsplit

import requests


NODE_DIR = Path(__file__).resolve().parents[1]


def load_helpers(path, names, namespace):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    tree.body = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names or isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id in names for target in node.targets)]
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            node.decorator_list = []
    exec(compile(tree, str(path), "exec"), namespace)


class ModelsTests(unittest.TestCase):
    def setUp(self):
        self.requests = SimpleNamespace(Session=Mock(), RequestException=type("RequestException", (Exception,), {}))
        self.response = Mock(status_code=200)
        self.response.__enter__ = Mock(return_value=self.response)
        self.response.__exit__ = Mock(return_value=False)
        self.session = Mock(trust_env=True)
        self.session.__enter__ = Mock(return_value=self.session)
        self.session.__exit__ = Mock(return_value=False)
        self.session.get.return_value = self.response
        self.requests.Session.return_value = self.session
        async def to_thread(function, *args):
            return function(*args)
        self.ns = {"os": os, "urlsplit": urlsplit, "requests": self.requests, "asyncio": SimpleNamespace(to_thread=to_thread),
                   "web": SimpleNamespace(json_response=lambda data, status=200: (status, data))}
        load_helpers(NODE_DIR / "llm_node.py", {"PROVIDERS", "_endpoint", "_error_detail"}, self.ns)
        load_helpers(NODE_DIR / "models.py", {"_models_endpoint", "_model_names", "_fetch_models", "list_models", "register_routes"}, self.ns)
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_provider_endpoints(self):
        endpoint = self.ns["_models_endpoint"]
        for provider, expected in (("LM Studio", "http://127.0.0.1:1234/api/v1/models"), ("Ollama", "http://127.0.0.1:11434/api/tags"), ("OpenRouter", "https://openrouter.ai/api/v1/models"), ("Groq", "https://api.groq.com/openai/v1/models"), ("Cerebras", "https://api.cerebras.ai/v1/models"), ("OpenCode", "https://opencode.ai/inference/v1/models")):
            with self.subTest(provider=provider):
                self.assertEqual(endpoint(provider, ""), expected)
        self.assertEqual(endpoint("OpenAI-compatible", "http://localhost:8080/v1/chat/completions"), "http://localhost:8080/v1/models")
        self.assertEqual(endpoint("OpenCode", "http://localhost:8080/v1"), "http://localhost:8080/v1/models")

    def test_native_model_formats(self):
        names = self.ns["_model_names"]
        self.assertEqual(names("LM Studio", {"models": [{"type": "llm", "key": "Qwen"}, {"type": "embedding", "key": "embed"}]}), ["Qwen"])
        self.assertEqual(names("Ollama", {"models": [{"name": "qwen:latest"}]}), ["qwen:latest"])
        self.assertEqual(names("OpenRouter", {"data": [{"id": "Z"}, {"id": "a"}, {"id": "Z"}, {}, 7]}), ["a", "Z"])
        self.assertEqual(names("OpenCode", {"data": [{"id": "chat", "supported_endpoints": ["/chat/completions"]}, {"id": "responses", "supported_endpoints": ["/responses"]}, {"id": "unspecified"}]}), ["chat", "unspecified"])
        with self.assertRaisesRegex(RuntimeError, "invalid model list"):
            names("OpenRouter", {"data": {}})

    def test_boundary_validation_and_local_no_request(self):
        fetch = self.ns["_fetch_models"]
        self.assertEqual(fetch({"provider": "ComfyUI Local"}), [])
        self.requests.Session.assert_not_called()
        for values in ([], {}, {"provider": "OpenRouter", "api_key": 3}, {"provider": "OpenRouter", "base_url": "https://key:secret@example.com"}, {"provider": "OpenRouter", "base_url": "file:///tmp"}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                fetch(values)
        self.requests.Session.assert_not_called()

    def test_credentials_request_policy(self):
        os.environ["CUSTOM_KEY"] = "environment-secret"
        self.response.json.return_value = {"data": [{"id": "chat"}]}
        self.assertEqual(self.ns["_fetch_models"]({"provider": "OpenRouter", "api_key_env": "CUSTOM_KEY"}), ["chat"])
        self.assertTrue(self.session.trust_env)
        options = self.session.get.call_args.kwargs
        self.assertEqual(options["headers"], {"Accept": "application/json", "Authorization": "Bearer environment-secret"})
        self.assertEqual(options["timeout"], 15)
        self.assertFalse(options["allow_redirects"])
        request = object()
        self.assertIs(options["auth"](request), request)
        self.ns["_fetch_models"]({"provider": "OpenRouter", "api_key_env": "CUSTOM_KEY", "api_key": "Bearer direct-secret"})
        self.assertEqual(self.session.get.call_args.kwargs["headers"]["Authorization"], "Bearer direct-secret")

    def test_requests_preserves_proxy_and_ca_without_netrc_override(self):
        self.ns["requests"] = requests
        os.environ["HTTPS_PROXY"] = "http://proxy.invalid:8080"
        os.environ["REQUESTS_CA_BUNDLE"] = "custom-ca.pem"
        response = requests.Response()
        response.status_code = 200
        response._content = b'{"object":"list","data":[{"id":"chat"}]}'
        with patch.object(requests.sessions, "get_netrc_auth") as netrc, patch.object(requests.sessions, "get_environ_proxies", return_value={"https": os.environ["HTTPS_PROXY"]}), patch.object(requests.Session, "send", return_value=response) as send:
            self.assertEqual(self.ns["_fetch_models"]({"provider": "Groq", "api_key": "groq-secret"}), ["chat"])
        netrc.assert_not_called()
        self.assertEqual(send.call_args.args[0].headers["Authorization"], "Bearer groq-secret")
        self.assertEqual(send.call_args.kwargs["proxies"]["https"], "http://proxy.invalid:8080")
        self.assertEqual(send.call_args.kwargs["verify"], "custom-ca.pem")
        self.assertFalse(send.call_args.kwargs["allow_redirects"])

    def test_cerebras_and_groq_authenticated_catalogs(self):
        for provider, env_name, model, owner in (("Cerebras", "CEREBRAS_API_KEY", "gpt-oss-120b", "Cerebras"), ("Groq", "GROQ_API_KEY", "llama-3.1-8b-instant", "Meta")):
            with self.subTest(provider=provider):
                os.environ[env_name] = "provider-secret"
                self.response.json.return_value = {"object": "list", "data": [{"id": model, "object": "model", "created": 0, "owned_by": owner, "active": True}]}
                self.assertEqual(self.ns["_fetch_models"]({"provider": provider}), [model])
                self.assertEqual(self.session.get.call_args.args[0], self.ns["PROVIDERS"][provider][0] + "/models")
                self.assertEqual(self.session.get.call_args.kwargs["headers"]["Authorization"], "Bearer provider-secret")

    def test_auth_errors_identify_provider_and_key_action(self):
        for provider in ("Cerebras", "Groq"):
            with self.subTest(provider=provider):
                self.response.status_code = 401
                self.response.json.return_value = {"error": {"message": "Invalid API Key", "type": "invalid_request_error", "code": "invalid_api_key"}}
                with self.assertRaises(RuntimeError) as caught:
                    self.ns["_fetch_models"]({"provider": provider})
                self.assertIn(provider, str(caught.exception))
                self.assertIn("HTTP 401", str(caught.exception))
                self.assertIn("API key or api_key_env", str(caught.exception))
        self.response.json.side_effect = ValueError("private gateway response")
        with self.assertRaisesRegex(RuntimeError, "HTTP 401"):
            self.ns["_fetch_models"]({"provider": "Groq"})

    def test_provider_errors_redact_both_credentials_and_env_name(self):
        os.environ["CUSTOM_KEY"] = "environment-secret"
        self.response.status_code = 401
        self.response.json.return_value = {"error": {"message": "direct-secret environment-secret CUSTOM_KEY"}}
        with self.assertRaises(RuntimeError) as caught:
            self.ns["_fetch_models"]({"provider": "OpenRouter", "api_key": "direct-secret", "api_key_env": "CUSTOM_KEY"})
        message = str(caught.exception)
        self.assertIn("HTTP 401", message)
        for secret in ("direct-secret", "environment-secret", "CUSTOM_KEY"):
            self.assertNotIn(secret, message)

    def test_connection_and_non_json_errors_are_actionable(self):
        self.session.get.side_effect = self.requests.RequestException("secret in URL")
        with self.assertRaisesRegex(RuntimeError, "Check the base URL, key"):
            self.ns["_fetch_models"]({"provider": "OpenRouter"})
        self.session.get.side_effect = None
        self.response.json.side_effect = ValueError("private response")
        with self.assertRaisesRegex(RuntimeError, "non-JSON response"):
            self.ns["_fetch_models"]({"provider": "OpenRouter"})

    def test_route_response_contract(self):
        def run(values):
            async def request_json():
                return values
            coroutine = self.ns["list_models"](SimpleNamespace(json=request_json))
            try:
                coroutine.send(None)
            except StopIteration as result:
                return result.value
            finally:
                coroutine.close()
            self.fail("Route unexpectedly yielded with immediate test dependencies.")
        self.assertEqual(run({"provider": "ComfyUI Local"}), (200, {"models": []}))
        status, data = run({"provider": "unknown"})
        self.assertEqual(status, 400)
        self.assertIn("error", data)
        self.response.status_code = 403
        self.response.json.return_value = {}
        status, data = run({"provider": "OpenRouter"})
        self.assertEqual(status, 502)
        self.assertIn("HTTP 403", data["error"])

    def test_route_registration(self):
        routes = Mock()
        self.ns["register_routes"](routes)
        routes.post.assert_called_once_with("/goat/models")
        routes.post.return_value.assert_called_once_with(self.ns["list_models"])


if __name__ == "__main__":
    unittest.main()
