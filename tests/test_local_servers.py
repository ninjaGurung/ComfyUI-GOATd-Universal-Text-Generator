import ast
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from threading import Thread
import unittest
from unittest.mock import Mock, patch

import requests

import test_context
from test_models import load_helpers, NODE_DIR
from test_security import security


class LocalServerTests(unittest.TestCase):
    def test_native_servers_use_loopback_without_environment_proxies(self):
        received = []

        class Handler(BaseHTTPRequestHandler):
            def reply(self, result):
                data = json.dumps(result).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                received.append((self.path, self.headers.get("Authorization"), None))
                self.reply({"models": [{"type": "llm", "key": "local-model", "name": "local-model"}]})

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                received.append((self.path, self.headers.get("Authorization"), payload))
                self.reply({"output": [{"type": "message", "content": "local answer"}], "message": {"content": "local answer"}, "choices": [{"message": {"content": "local answer"}}]})

            def log_message(self, *args):
                pass

        namespace = dict(test_context.namespace, requests=requests, add_media=Mock(), session_keys=security.SessionKeys())
        exec(compile(ast.Module(body=test_context.definitions, type_ignores=[]), "llm_node.py", "exec"), namespace)
        method = next(item for cls in test_context.SOURCE.body if isinstance(cls, ast.ClassDef) and cls.name == "UniversalLLM" for item in cls.body if isinstance(item, ast.FunctionDef) and item.name == "generate")
        method.decorator_list = []
        exec(compile(ast.Module(body=[method], type_ignores=[]), "llm_node.py", "exec"), namespace)
        load_helpers(NODE_DIR / "models.py", {"_models_endpoint", "_model_names", "_fetch_models"}, namespace)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.dict(os.environ, {"HTTP_PROXY": "http://127.0.0.1:1", "HTTPS_PROXY": "http://127.0.0.1:1", "ALL_PROXY": "http://127.0.0.1:1", "NO_PROXY": ""}, clear=True):
                base = f"http://LOCALHOST:{server.server_port}"
                for provider, path in (("LM Studio", "/api/v1"), ("Ollama", "/api"), ("OpenAI-compatible", "/v1")):
                    url = base + path if provider == "OpenAI-compatible" else base
                    endpoint = namespace["_endpoint"](provider, url)
                    reference = namespace["session_keys"].put(provider, endpoint, "local-dummy-key")
                    if provider != "OpenAI-compatible":
                        self.assertEqual(namespace["_fetch_models"]({"provider": provider, "base_url": url, "api_key": reference}), ["local-model"])
                    output = namespace["generate"](None, provider=provider, model="local-model", base_url=url, api_key=reference, prompt_idea="hello", system_prompt="be concise", timeout_seconds=5)
                    self.assertEqual(output, ("local answer",))
                    self.assertIn((path + ("/chat/completions" if provider == "OpenAI-compatible" else "/chat")), [item[0] for item in received])
            self.assertTrue(all(auth == "Bearer local-dummy-key" for _, auth, _ in received))
            self.assertFalse(next(payload for path, _, payload in received if path == "/api/v1/chat")["store"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
