# Author: ninjaGurung
# GitHub: https://github.com/ninjaGurung

import asyncio
import json
from urllib.parse import urlsplit

from aiohttp import web
import requests

from .llm_node import PROVIDERS, _endpoint, _error_detail, _resolve_key
from .security import is_loopback, protect_route, session_keys


def _models_endpoint(provider, base_url):
    endpoint = _endpoint(provider, base_url)
    if provider == "LM Studio":
        return endpoint.removesuffix("/chat") + "/models"
    if provider == "Ollama":
        return endpoint.removesuffix("/chat") + "/tags"
    if provider == "OpenCode" and endpoint == PROVIDERS[provider][0] + "/chat/completions":
        return "https://opencode.ai/inference/v1/models"
    return endpoint.removesuffix("/chat/completions") + "/models"


def _model_names(provider, result):
    field = "models" if provider in ("LM Studio", "Ollama") else "data"
    if not isinstance(result, dict) or not isinstance(result.get(field), list):
        raise RuntimeError("Model server returned an invalid model list.")
    names = []
    for item in result[field]:
        if not isinstance(item, dict):
            continue
        if provider == "LM Studio" and item.get("type") != "llm":
            continue
        if provider == "OpenCode":
            endpoints = item.get("supported_endpoints")
            if isinstance(endpoints, list) and not any(isinstance(value, str) and value.endswith("/chat/completions") for value in endpoints):
                continue
        name = item.get("key" if provider == "LM Studio" else "name" if provider == "Ollama" else "id")
        if isinstance(name, str) and name.strip() and name not in names:
            names.append(name)
    return sorted(names, key=str.casefold)


def _fetch_models(values):
    if not isinstance(values, dict):
        raise ValueError("Send model settings as a JSON object.")
    for field in ("provider", "base_url", "api_key", "api_key_env"):
        if not isinstance(values.get(field, ""), str):
            raise ValueError(f"{field} must be text.")
    provider = values.get("provider", "")
    if provider == "ComfyUI Local":
        return []
    if provider not in PROVIDERS:
        raise ValueError("Choose a supported LLM provider.")
    endpoint = _models_endpoint(provider, values.get("base_url", ""))
    direct_key = values.get("api_key", "").strip()
    key = _resolve_key(provider, _endpoint(provider, values.get("base_url", "")), direct_key, values.get("api_key_env", ""))
    headers = {"Accept": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    try:
        with requests.Session() as session:
            with session.get(endpoint, headers=headers, timeout=15, allow_redirects=False, auth=lambda request: request, proxies={"http": "", "https": ""} if is_loopback(urlsplit(endpoint).hostname) else None) as response:
                try:
                    result = response.json()
                except ValueError:
                    if response.status_code == 200:
                        raise RuntimeError("Model server returned a non-JSON response. Check the base URL.") from None
                    result = {}
                if response.status_code != 200:
                    detail = _error_detail(result, key)
                    for secret in (direct_key, values.get("api_key_env", "")):
                        if secret:
                            detail = detail.replace(secret, "[redacted]")
                    hint = " Check this provider's API key or api_key_env." if response.status_code in (401, 403) else ""
                    raise RuntimeError(f"{provider} model request failed (HTTP {response.status_code}).{hint}" + (f" {detail}" if detail else ""))
    except requests.RequestException:
        raise RuntimeError("Could not fetch models. Check the base URL, key, and server availability.") from None
    return _model_names(provider, result)


async def list_models(request):
    try:
        values = await request.json()
        models = await asyncio.to_thread(_fetch_models, values)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except RuntimeError as exc:
        return web.json_response({"error": str(exc)}, status=502)
    return web.json_response({"models": models})


def register_routes(routes):
    routes.post("/goat/models")(protect_route(list_models))
    routes.post("/goat/credentials")(protect_route(store_key))


async def store_key(request):
    data = bytearray()
    async for chunk in request.content.iter_chunked(4096):
        data.extend(chunk)
        if len(data) > 16384:
            raise web.HTTPRequestEntityTooLarge(max_size=16384, actual_size=len(data))
    try:
        values = json.loads(data)
        if not isinstance(values, dict) or any(not isinstance(values.get(name, ""), str) for name in ("provider", "base_url", "api_key")):
            raise ValueError("Send provider, base_url, and api_key as text.")
        provider = values.get("provider", "")
        if provider not in PROVIDERS:
            raise ValueError("Choose an API provider.")
        endpoint = _endpoint(provider, values.get("base_url", ""))
        key = values.get("api_key", "").strip()
        if not key or key.startswith("goat-session:") or "\r" in key or "\n" in key:
            raise ValueError("Enter a valid provider API key.")
        reference = session_keys.put(provider, endpoint, key)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    return web.json_response({"reference": reference})
