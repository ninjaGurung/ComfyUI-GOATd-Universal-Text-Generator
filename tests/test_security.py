import asyncio
import importlib.util
import os
from pathlib import Path
from time import monotonic
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer, make_mocked_request


spec = importlib.util.spec_from_file_location("goat_security_test", Path(__file__).resolve().parents[1] / "security.py")
security = importlib.util.module_from_spec(spec)
spec.loader.exec_module(security)


class KeyStoreTests(unittest.TestCase):
    def test_references_are_scoped_expire_and_never_contain_the_key(self):
        store = security.SessionKeys()
        reference = store.put("provider", "https://one.invalid/chat", "dummy-secret")
        self.assertNotIn("dummy-secret", reference)
        self.assertEqual(store.get(reference, "provider", "https://one.invalid/chat"), "dummy-secret")
        for provider, endpoint in (("other", "https://one.invalid/chat"), ("provider", "https://two.invalid/chat")):
            with self.assertRaises(ValueError):
                store.get(reference, provider, endpoint)
        store.entries[reference] = (monotonic() - 3601, *store.entries[reference][1:])
        with self.assertRaises(ValueError):
            store.get(reference, "provider", "https://one.invalid/chat")

    def test_repeated_registration_reuses_reference_and_storage_is_bounded(self):
        store = security.SessionKeys()
        first = store.put("p", "https://one.invalid", "secret")
        self.assertEqual(first, store.put("p", "https://one.invalid", "secret"))
        for index in range(130):
            store.put("p", "https://one.invalid", str(index))
        self.assertEqual(len(store.entries), 128)
        self.assertNotIn(first, store.entries)


class RouteSecurityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        env = patch.dict(os.environ, {}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.called = False

    async def handler(self, request):
        self.called = True
        return web.json_response({"ok": True})

    def request(self, host="127.0.0.1:8188", peer="127.0.0.1", **headers):
        transport = Mock()
        transport.get_extra_info.return_value = (peer, 1234)
        return make_mocked_request("POST", "/goat/models", headers={"Host": host, "X-GOAT-Request": "1", **headers}, transport=transport)

    async def test_local_browser_allowed_and_response_not_cached(self):
        response = await security.protect_route(self.handler)(self.request(Origin="http://127.0.0.1:8188"))
        self.assertTrue(self.called)
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    async def test_cross_origin_rebinding_remote_and_missing_header_blocked(self):
        for request in (self.request(Origin="https://evil.invalid"), self.request(host="evil.invalid"), self.request(peer="192.168.1.10"), self.request(**{"X-GOAT-Request": "", "Sec-Fetch-Site": "cross-site"}), self.request(**{"Sec-Fetch-Site": "same-site"})):
            with self.assertRaises(web.HTTPForbidden):
                await security.protect_route(self.handler)(request)
        self.assertFalse(self.called)

    async def test_remote_requires_configured_token_and_checks_origin(self):
        os.environ["GOAT_ROUTE_TOKEN"] = "owner-access-token"
        route = security.protect_route(self.handler)
        with self.assertRaises(web.HTTPUnauthorized):
            await route(self.request(host="comfy.example", peer="192.168.1.10").clone(scheme="https"))
        with self.assertRaises(web.HTTPUnauthorized):
            await route(self.request(**{"X-GOAT-Token": "wrong"}))
        response = await route(self.request(host="comfy.example", peer="192.168.1.10", Origin="https://comfy.example", **{"X-GOAT-Token": "owner-access-token"}).clone(scheme="https"))
        self.assertEqual(response.status, 200)
        with self.assertRaises(web.HTTPForbidden):
            await route(self.request(host="comfy.example", peer="192.168.1.10", **{"X-GOAT-Token": "owner-access-token"}).clone(scheme="http"))

    async def test_real_http_route_blocks_request_before_handler(self):
        app = web.Application()
        app.router.add_get("/goat/test", security.protect_route(self.handler))
        async with TestClient(TestServer(app)) as client:
            response = await client.get("/goat/test")
            self.assertEqual(response.status, 403)
            self.assertFalse(self.called)
            response = await client.get("/goat/test", headers={"X-GOAT-Request": "1"})
            self.assertEqual(response.status, 200)


if __name__ == "__main__":
    unittest.main()
