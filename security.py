import ipaddress
import os
from collections import OrderedDict
from functools import wraps
from secrets import compare_digest, token_urlsafe
from threading import Lock
from time import monotonic
from urllib.parse import urlsplit

from aiohttp import web


def is_loopback(host):
    if host == "localhost":
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address.is_loopback


def protect_route(handler):
    @wraps(handler)
    async def checked(request):
        if request.headers.get("X-GOAT-Request") != "1" or request.headers.get("Sec-Fetch-Site", "same-origin") != "same-origin":
            raise web.HTTPForbidden(text="Use the GOAT extension from the ComfyUI page.")
        origin = request.headers.get("Origin")
        if origin:
            try:
                parsed = urlsplit(origin)
            except ValueError:
                raise web.HTTPForbidden(text="Invalid GOAT request origin.") from None
            if parsed.scheme not in ("http", "https") or parsed.netloc.lower() != request.host.lower():
                raise web.HTTPForbidden(text="Cross-origin GOAT requests are not allowed.")
        token = os.environ.get("GOAT_ROUTE_TOKEN", "")
        peer = request.transport.get_extra_info("peername") if request.transport else None
        if token:
            if not peer or (not is_loopback(peer[0]) and not request.secure):
                raise web.HTTPForbidden(text="Remote GOAT access requires HTTPS or a local TLS reverse proxy.")
            if not compare_digest(request.headers.get("X-GOAT-Token", "").encode(), token.encode()):
                raise web.HTTPUnauthorized(text="Enter the GOAT_ROUTE_TOKEN configured on the ComfyUI server.")
        else:
            try:
                host = urlsplit("//" + request.host).hostname or ""
            except ValueError:
                raise web.HTTPForbidden(text="Invalid GOAT request host.") from None
            if not peer or not is_loopback(peer[0]) or not is_loopback(host):
                raise web.HTTPForbidden(text="Remote GOAT access requires GOAT_ROUTE_TOKEN on the server.")
        response = await handler(request)
        response.headers["Cache-Control"] = "no-store"
        return response
    return checked


class SessionKeys:
    def __init__(self):
        self.entries = OrderedDict()
        self.lock = Lock()

    def _expire(self):
        now = monotonic()
        for reference, entry in list(self.entries.items()):
            if now - entry[0] > 3600:
                del self.entries[reference]

    def put(self, provider, endpoint, key):
        with self.lock:
            self._expire()
            reference = next((ref for ref, entry in self.entries.items() if entry[1:] == (provider, endpoint, key)), None)
            reference = reference or "goat-session:" + token_urlsafe(32)
            self.entries[reference] = (monotonic(), provider, endpoint, key)
            self.entries.move_to_end(reference)
            while len(self.entries) > 128:
                self.entries.popitem(last=False)
            return reference

    def get(self, reference, provider, endpoint):
        with self.lock:
            self._expire()
            entry = self.entries.get(reference)
            if not entry or entry[1:3] != (provider, endpoint):
                raise ValueError("API key session expired or belongs to another endpoint. Re-enter the key.")
            self.entries[reference] = (monotonic(), *entry[1:])
            self.entries.move_to_end(reference)
            return entry[3]


session_keys = SessionKeys()
