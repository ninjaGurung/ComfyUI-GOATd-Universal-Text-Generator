import ast
import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlsplit

import requests
from aiohttp import web as aio_web
from aiohttp.test_utils import TestClient, TestServer
from test_security import security


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
        self.ns = {"os": os, "json": json, "urlsplit": urlsplit, "requests": self.requests, "asyncio": SimpleNamespace(to_thread=to_thread),
                   "web": SimpleNamespace(json_response=lambda data, status=200: (status, data)),
                   "is_loopback": security.is_loopback, "session_keys": security.SessionKeys(), "protect_route": security.protect_route}
        load_helpers(NODE_DIR / "llm_node.py", {"PROVIDERS", "_endpoint", "_resolve_key", "_error_detail"}, self.ns)
        load_helpers(NODE_DIR / "models.py", {"_models_endpoint", "_model_names", "_fetch_models", "list_models", "register_routes", "store_key"}, self.ns)
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_provider_endpoints(self):
        endpoint = self.ns["_models_endpoint"]
        for provider, expected in (("LM Studio", "http://127.0.0.1:1234/api/v1/models"), ("Ollama", "http://127.0.0.1:11434/api/tags"), ("OpenRouter", "https://openrouter.ai/api/v1/models"), ("Groq", "https://api.groq.com/openai/v1/models"), ("Cerebras", "https://api.cerebras.ai/v1/models"), ("OpenCode", "https://opencode.ai/inference/v1/models")):
            with self.subTest(provider=provider):
                self.assertEqual(endpoint(provider, ""), expected)
        self.assertEqual(endpoint("OpenAI-compatible", "https://custom.example/v1/chat/completions"), "https://custom.example/v1/models")
        self.assertEqual(endpoint("OpenCode", "https://custom.example/v1"), "https://custom.example/v1/models")

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
        os.environ["OPENROUTER_API_KEY"] = "environment-secret"
        self.response.json.return_value = {"data": [{"id": "chat"}]}
        self.assertEqual(self.ns["_fetch_models"]({"provider": "OpenRouter", "api_key_env": "OPENROUTER_API_KEY"}), ["chat"])
        self.assertTrue(self.session.trust_env)
        options = self.session.get.call_args.kwargs
        self.assertEqual(options["headers"], {"Accept": "application/json", "Authorization": "Bearer environment-secret"})
        self.assertEqual(options["timeout"], 15)
        self.assertFalse(options["allow_redirects"])
        request = object()
        self.assertIs(options["auth"](request), request)
        self.ns["_fetch_models"]({"provider": "OpenRouter", "api_key_env": "OPENROUTER_API_KEY", "api_key": "Bearer direct-secret"})
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

    def test_provider_errors_redact_used_credential_and_env_name(self):
        os.environ["CUSTOM_KEY"] = "environment-secret"
        self.response.status_code = 401
        self.response.json.return_value = {"error": {"message": "direct-secret CUSTOM_KEY"}}
        with self.assertRaises(RuntimeError) as caught:
            self.ns["_fetch_models"]({"provider": "OpenRouter", "api_key": "direct-secret", "api_key_env": "CUSTOM_KEY"})
        message = str(caught.exception)
        self.assertIn("HTTP 401", message)
        for secret in ("direct-secret", "CUSTOM_KEY"):
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
        self.assertEqual([call.args[0] for call in routes.post.call_args_list], ["/goat/models", "/goat/credentials"])
        self.assertEqual(routes.post.return_value.call_args_list[0].args[0].__wrapped__, self.ns["list_models"])

    def test_arbitrary_environment_lookup_and_credential_forwarding_are_blocked(self):
        os.environ["PRIVATE_SECRET"] = "must-never-leave"
        os.environ["OPENROUTER_API_KEY"] = "provider-secret"
        fetch = self.ns["_fetch_models"]
        with self.assertRaisesRegex(ValueError, "restricted"):
            fetch({"provider": "OpenRouter", "api_key_env": "PRIVATE_SECRET", "base_url": "https://attacker.invalid/v1"})
        self.requests.Session.assert_not_called()
        self.response.json.return_value = {"data": []}
        fetch({"provider": "OpenRouter", "base_url": "https://attacker.invalid/v1"})
        self.assertNotIn("Authorization", self.session.get.call_args.kwargs["headers"])
        with self.assertRaisesRegex(ValueError, "restricted"):
            fetch({"provider": "OpenRouter", "api_key_env": "OPENROUTER_API_KEY", "base_url": "https://attacker.invalid/v1"})

    def test_http_is_limited_to_local_native_servers(self):
        endpoint = self.ns["_endpoint"]
        for provider in ("LM Studio", "Ollama", "OpenAI-compatible"):
            for host in ("127.0.0.1", "localhost", "[::1]"):
                self.assertTrue(endpoint(provider, "http://" + host + ":1234").startswith("http://"))
        for provider in self.ns["PROVIDERS"]:
            for host in ("api.example", "192.168.1.2", "localhost.attacker.invalid"):
                with self.assertRaisesRegex(ValueError, "HTTPS"):
                    endpoint(provider, "http://" + host + ":1234")
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            endpoint("OpenRouter", "http://127.0.0.1:8080/v1")

    def test_session_key_cannot_be_replayed_to_another_endpoint(self):
        endpoint = self.ns["_endpoint"]("OpenRouter", "")
        reference = self.ns["session_keys"].put("OpenRouter", endpoint, "session-secret")
        self.response.json.return_value = {"data": []}
        self.ns["_fetch_models"]({"provider": "OpenRouter", "api_key": reference})
        self.assertEqual(self.session.get.call_args.kwargs["headers"]["Authorization"], "Bearer session-secret")
        with self.assertRaises(ValueError):
            self.ns["_fetch_models"]({"provider": "OpenRouter", "api_key": reference, "base_url": "https://attacker.invalid/v1"})

    def test_server_owned_binding_supports_custom_environment_credentials(self):
        os.environ["MY_LOCAL_KEY"] = "local-secret"
        os.environ["GOAT_API_KEY_BINDINGS"] = json.dumps({"http://127.0.0.1:1234/api/v1/chat": "MY_LOCAL_KEY"})
        self.response.json.return_value = {"models": []}
        self.ns["_fetch_models"]({"provider": "LM Studio", "api_key_env": "MY_LOCAL_KEY"})
        options = self.session.get.call_args.kwargs
        self.assertEqual(options["headers"]["Authorization"], "Bearer local-secret")
        self.assertEqual(options["proxies"], {"http": "", "https": ""})
        with self.assertRaises(ValueError):
            self.ns["_fetch_models"]({"provider": "LM Studio", "api_key_env": "MY_LOCAL_KEY", "base_url": "https://attacker.invalid"})

    def test_credential_route_protects_registration_and_returns_only_a_reference(self):
        self.ns["web"] = aio_web
        async def run():
            routes = aio_web.RouteTableDef()
            self.ns["register_routes"](routes)
            app = aio_web.Application()
            app.add_routes(routes)
            async with TestClient(TestServer(app)) as client:
                values = {"provider": "OpenRouter", "api_key": "dummy-private-key"}
                response = await client.post("/goat/credentials", json=values)
                self.assertEqual(response.status, 403)
                self.assertEqual(len(self.ns["session_keys"].entries), 0)
                response = await client.post("/goat/credentials", json=values, headers={"X-GOAT-Request": "1"})
                self.assertEqual(response.status, 200)
                result = await response.json()
                self.assertNotIn("dummy-private-key", json.dumps(result))
                self.assertEqual(self.ns["session_keys"].get(result["reference"], "OpenRouter", self.ns["_endpoint"]("OpenRouter", "")), "dummy-private-key")
                response = await client.post("/goat/credentials", data=b"x" * 16385, headers={"X-GOAT-Request": "1"})
                self.assertEqual(response.status, 413)
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
