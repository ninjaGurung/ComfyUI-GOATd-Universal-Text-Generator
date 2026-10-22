# Author: ninjaGurung
# GitHub: https://github.com/ninjaGurung

import asyncio
import os

from aiohttp import web
import requests

from .llm_node import PROVIDERS, _endpoint, _error_detail


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
    env_name = values.get("api_key_env", "").strip() or PROVIDERS[provider][1]
    env_key = os.environ.get(env_name, "").strip()
    direct_key = values.get("api_key", "").strip()
    key = direct_key or env_key
    if key.lower().startswith("bearer "):
        key = key[7:].strip()
    headers = {"Accept": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    try:
        with requests.Session() as session:
            with session.get(endpoint, headers=headers, timeout=15, allow_redirects=False, auth=lambda request: request) as response:
                try:
                    result = response.json()
                except ValueError:
                    if response.status_code == 200:
                        raise RuntimeError("Model server returned a non-JSON response. Check the base URL.") from None
                    result = {}
                if response.status_code != 200:
                    detail = _error_detail(result, key)
                    for secret in (direct_key, env_key, env_name):
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
    routes.post("/goat/models")(list_models)
