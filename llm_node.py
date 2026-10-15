# Author: ninjaGurung
# GitHub: https://github.com/ninjaGurung

import os
from urllib.parse import urlsplit

import requests

from comfy_api.latest import io, ui

from .media import add_media
from .local import generate_local
from .prompt_guides import GUIDE_OPTIONS, combine_system_prompt
from .stop_sequences import parse_stop_sequences


PROVIDERS = {
    "OpenRouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "Cerebras": ("https://api.cerebras.ai/v1", "CEREBRAS_API_KEY"),
    "LM Studio": ("http://127.0.0.1:1234", "LM_STUDIO_API_KEY"),
    "Ollama": ("http://127.0.0.1:11434", "OLLAMA_API_KEY"),
    "OpenAI-compatible": ("", "OPENAI_API_KEY"),
    "Groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY"),
}


def _endpoint(provider, base_url):
    url = (base_url.strip() or PROVIDERS[provider][0]).rstrip("/")
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Set base_url to an HTTP(S) server URL.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Use a base_url without credentials, query parameters, or fragments.")
    if provider == "Ollama":
        return url if parsed.path.endswith("/api/chat") else url + ("/chat" if parsed.path.endswith("/api") else "/api/chat")
    if provider == "LM Studio":
        return url if parsed.path.endswith("/api/v1/chat") else url + ("/chat" if parsed.path.endswith("/api/v1") else "/api/v1/chat")
    return url if parsed.path.endswith("/chat/completions") else url + "/chat/completions"


def _payload(provider, model, prompt_idea, system_prompt, thinking, reasoning_effort, max_output_tokens, context_window, temperature, top_p, top_k, min_p, repetition_penalty, frequency_penalty, presence_penalty, seed, stop_sequences):
    sampling = {}
    if temperature >= 0:
        sampling["temperature"] = temperature
    if top_p >= 0:
        sampling["top_p"] = top_p
    if provider in ("OpenRouter", "LM Studio", "Ollama"):
        if top_k >= 0:
            sampling["top_k"] = top_k
        if min_p >= 0:
            sampling["min_p"] = min_p
        if repetition_penalty >= 0:
            sampling["repetition_penalty" if provider == "OpenRouter" else "repeat_penalty"] = repetition_penalty
    if provider != "LM Studio":
        if seed >= 0:
            sampling["seed"] = seed
        stop = parse_stop_sequences(stop_sequences)
        if stop:
            sampling["stop"] = stop
    if provider in ("OpenRouter", "Cerebras", "OpenAI-compatible"):
        if frequency_penalty != 0:
            sampling["frequency_penalty"] = frequency_penalty
        if presence_penalty != 0:
            sampling["presence_penalty"] = presence_penalty

    if provider == "LM Studio":
        body = {"model": model, "input": prompt_idea, "stream": False, "store": False, "max_output_tokens": max_output_tokens, **sampling}
        if system_prompt:
            body["system_prompt"] = system_prompt
        if context_window > 0:
            body["context_length"] = context_window
        if thinking != "auto":
            body["reasoning"] = "off" if thinking == "disabled" else ("on" if reasoning_effort == "auto" else reasoning_effort)
        return body

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt_idea})
    body = {"model": model, "messages": messages, "stream": False}
    if provider == "Ollama":
        body["options"] = {"num_predict": max_output_tokens, **sampling}
        if context_window > 0:
            body["options"]["num_ctx"] = context_window
        if thinking != "auto":
            body["think"] = False if thinking == "disabled" else (True if reasoning_effort == "auto" else reasoning_effort)
    else:
        body.update(sampling)
        body["max_tokens" if provider == "OpenAI-compatible" else "max_completion_tokens"] = max_output_tokens
        if thinking != "auto":
            if provider == "Groq":
                if model in ("openai/gpt-oss-20b", "openai/gpt-oss-120b"):
                    if thinking == "disabled":
                        raise ValueError("Groq GPT-OSS cannot disable thinking. Use auto or enabled with low reasoning_effort.")
                    body["reasoning_effort"] = "medium" if reasoning_effort == "auto" else reasoning_effort
                elif model.startswith("qwen/qwen3"):
                    if thinking == "disabled":
                        body["reasoning_effort"] = "none"
                    elif model == "qwen/qwen3.8-27b":
                        body["reasoning_effort"] = "medium" if reasoning_effort == "auto" else reasoning_effort
                    else:
                        body["reasoning_effort"] = "default"
            elif provider == "Cerebras" and model == "gpt-oss-120b":
                body["reasoning_effort"] = "low" if thinking == "disabled" else ("medium" if reasoning_effort == "auto" else reasoning_effort)
                if thinking == "disabled":
                    body["reasoning_format"] = "hidden"

            elif provider == "OpenRouter":
                body["reasoning"] = {"enabled": thinking == "enabled"}
                if thinking == "enabled" and reasoning_effort != "auto":
                    body["reasoning"]["effort"] = reasoning_effort
            else:
                body["reasoning_effort"] = "none" if thinking == "disabled" else ("medium" if reasoning_effort == "auto" else reasoning_effort)
    if provider == "Groq":
        if model in ("openai/gpt-oss-20b", "openai/gpt-oss-120b"):
            body["include_reasoning"] = False
        elif model.startswith("qwen/qwen3") or model == "minimaxai/minimax-m2.7":
            body["reasoning_format"] = "hidden"
    return body


def _answer(provider, result):
    if not isinstance(result, dict):
        raise RuntimeError("LLM server returned an invalid response object.")
    if "error" in result:
        raise RuntimeError("LLM server reported an error. Check the model, credentials, and settings.")
    try:
        if provider == "LM Studio":
            content = "\n".join(item["content"] for item in result["output"] if item["type"] == "message")
        elif provider == "Ollama":
            content = result["message"]["content"]
        else:
            choice = result["choices"][0]
            message = choice["message"]
            if not isinstance(message, dict):
                raise TypeError("Expected a message object")
            content = message.get("content")
            if choice["message"].get("refusal"):
                raise RuntimeError("LLM provider refused this prompt. Revise the prompt or choose another model.")
            if isinstance(content, list):
                content = "".join(item["text"] for item in content if item["type"] in ("text", "output_text"))
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("LLM server response did not contain the expected answer text. Check the provider and endpoint.") from exc
    if content is None or isinstance(content, str) and not content.strip():
        raise RuntimeError("LLM returned no final answer. Increase max_output_tokens or disable thinking if the model supports it.")
    if not isinstance(content, str):
        raise RuntimeError("LLM returned an unsupported answer format. Check the provider and endpoint.")
    return content


class UniversalLLM(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="UniversalLLM",
            display_name="Universal LLM",
            is_deprecated=True,
            is_output_node=False,
            category="LLM",
            description="Generate text with hosted or local LLMs. Reference connected media with @image1, @video1, or @audio1. Connect text to a STRING input, including CLIP Text Encode.",
            search_aliases=["openrouter", "cerebras", "ollama", "lm studio", "language model"],
            inputs=[
                io.Combo.Input("provider", options=[*PROVIDERS, "ComfyUI Local"], default="OpenRouter"),
                io.String.Input("model", default="", tooltip="Model ID from your provider or local server. ComfyUI Local uses the connected CLIP model instead. Media inputs require a compatible multimodal model."),
                io.String.Input("prompt_idea", multiline=True, default="", dynamic_prompts=False, tooltip="Reference attachments by socket name: @image1, @video1, @audio1. All connected media are sent."),
                io.String.Input("system_prompt", multiline=True, default="You are a prompt enhancement assistant. Follow the prompt guide for the selected model and the user's explicit instructions. Return only the final enhanced prompt in the guide's required format. Strictly no commentary, preambles, explanations, analysis, suggestions, mode labels, or copied guide headings. Preserve the user's intent, details, wording, and reference associations. Use only information explicitly supplied by the user or directly observable in the supplied references. Do not invent details, make assumptions, infer unstated intentions, or fill gaps. Leave unspecified details unspecified. Improve clarity, wording, and structure without adding content. Treat guide examples as examples, never as supplied assets or requested details. When references are connected, explicitly identify their roles using the selected guide's reference syntax. These constraints take precedence over any creative embellishment suggested by the guide. If no guide is selected, enhance the prompt using these same constraints.", dynamic_prompts=False, tooltip="System instructions. Attachment tags can also be used here."),
                io.String.Input("api_key", optional=True, default="", tooltip="Direct keys are saved in workflows. Leave blank to use an environment variable; local servers usually need no key."),
                io.String.Input("api_key_env", optional=True, default="", tooltip="Environment variable name. Blank uses OPENROUTER_API_KEY, CEREBRAS_API_KEY, LM_STUDIO_API_KEY, OLLAMA_API_KEY, OPENAI_API_KEY, or GROQ_API_KEY."),
                io.String.Input("base_url", optional=True, default="", tooltip="Blank uses the provider default. Custom OpenAI-compatible servers require a URL including the API prefix, e.g. http://localhost:8080/v1. Full endpoint URLs also work."),
                io.Combo.Input("thinking", optional=True, options=["auto", "enabled", "disabled"], default="auto", tooltip="Auto uses the model default. Requires model support. Cerebras GPT-OSS disabled uses low effort with hidden reasoning; it cannot turn reasoning off. Groq GPT-OSS always reasons: use auto/enabled, never disabled. Groq Qwen3 supports disabled; unsupported models omit thinking controls."),
                io.Combo.Input("reasoning_effort", optional=True, options=["auto", "low", "medium", "high"], default="auto", tooltip="Used when thinking is enabled. Groq low/medium/high apply to GPT-OSS 20B/120B and Qwen3.8 27B; other Qwen3 models use their default effort."),
                io.Int.Input("max_output_tokens", optional=True, default=1024, min=1, max=2147483647, tooltip="Generation budget. Reasoning may consume part of this budget."),
                io.Int.Input("context_window", optional=True, default=0, min=0, max=2147483647, tooltip="LM Studio and Ollama only; 0 uses the server default. Hosted providers control their context limits."),
                io.Float.Input("temperature", optional=True, default=0.7, min=-1.0, max=2.0, step=0.05, tooltip="Higher is more varied; 0 is focused. -1 uses the provider default."),
                io.Float.Input("top_p", optional=True, default=-1.0, min=-1.0, max=1.0, step=0.05, tooltip="Nucleus sampling. -1 uses the provider default."),
                io.Int.Input("top_k", optional=True, default=-1, min=-1, max=2147483647, tooltip="OpenRouter, LM Studio, Ollama, ComfyUI Local. -1 uses the provider default."),
                io.Float.Input("min_p", optional=True, default=-1.0, min=-1.0, max=1.0, step=0.01, tooltip="OpenRouter, LM Studio, Ollama, ComfyUI Local. -1 uses the provider default."),
                io.Float.Input("repetition_penalty", optional=True, default=-1.0, min=-1.0, max=10.0, step=0.05, tooltip="OpenRouter, LM Studio, Ollama, ComfyUI Local. 1 is neutral; -1 uses the provider default."),
                io.Float.Input("frequency_penalty", optional=True, default=0.0, min=-2.0, max=2.0, step=0.1, tooltip="OpenRouter, Cerebras, OpenAI-compatible. Positive values discourage frequent tokens. Unsupported by Groq."),
                io.Float.Input("presence_penalty", optional=True, default=0.0, min=-2.0, max=2.0, step=0.1, tooltip="OpenRouter, Cerebras, OpenAI-compatible, ComfyUI Local. Positive values encourage new topics. Unsupported by Groq."),
                io.Int.Input("seed", optional=True, default=-1, min=-1, max=2147483647, tooltip="-1 leaves the seed unset. Not supported by LM Studio's native API. Determinism depends on the provider."),
                io.String.Input("stop_sequences", optional=True, default="", dynamic_prompts=False, tooltip='Optional literal stop text, or a JSON array such as ["</s>", "\\nUser:"]. Legacy newline-separated lists also work. ComfyUI Local trims after generation; LM Studio native API does not support stops.'),
                io.Int.Input("timeout_seconds", optional=True, default=120, min=1, max=3600, tooltip="Connection/read timeout; allow more time for local thinking models."),
                io.Boolean.Input("force_regenerate", optional=True, default=False, tooltip="Make a new request each time this node is executed, even if inputs match the cached result."),
                io.Combo.Input("video_mode", optional=True, options=["auto", "native", "frames"], default="auto", tooltip="Auto sends native video on OpenRouter/custom servers and sampled frames on image-only providers. Frame mode analyzes visuals without the soundtrack."),
                io.Int.Input("video_frame_count", optional=True, default=0, min=0, max=2147483647, tooltip="Frames per video in frame mode. 0 uses 2 for Cerebras/Groq and 12 for other providers. Positive values are sent unchanged; provider image limits also count other attachments."),
                io.Autogrow.Input("images", template=io.Autogrow.TemplateNames(io.Image.Input("image"), names=[f"image{i}" for i in range(1, 11)], min=0), optional=True, tooltip="Connect up to 10 IMAGE sockets. Reference each socket's complete batch with @image1, @image2, etc."),
                io.Autogrow.Input("videos", template=io.Autogrow.TemplateNames(io.Video.Input("video"), names=[f"video{i}" for i in range(1, 5)], min=0), optional=True, tooltip="Connect up to 4 VIDEO sockets. Reference them with @video1, @video2, etc."),
                io.Autogrow.Input("audios", template=io.Autogrow.TemplateNames(io.Audio.Input("audio"), names=[f"audio{i}" for i in range(1, 7)], min=0), optional=True, tooltip="Connect up to 6 AUDIO sockets. Reference each socket's complete batch with @audio1, @audio2, etc. Requires OpenRouter, a compatible custom server, or an audio-capable ComfyUI Local model."),
                io.Clip.Input("clip", optional=True, tooltip="For ComfyUI Local: connect a generation-capable model from Load CLIP or a compatible GGUF loader. Standard CLIP and encoder-only T5 cannot generate text."),
            ],
            outputs=[io.String.Output("text", display_name="text")],
        )

    @classmethod
    def IS_CHANGED(cls, force_regenerate=False, **kwargs):
        return float("nan") if force_regenerate else ""

    @classmethod
    def fingerprint_inputs(cls, force_regenerate=False, **kwargs):
        return cls.IS_CHANGED(force_regenerate=force_regenerate)

    @classmethod
    def execute(cls, **kwargs):
        text, = cls.generate(**kwargs)
        return io.NodeOutput(text, ui=ui.PreviewText(text))

    @classmethod
    def generate(cls, provider, model, prompt_idea, system_prompt, api_key="", api_key_env="", base_url="", thinking="auto", reasoning_effort="auto", max_output_tokens=1024, context_window=0, temperature=0.7, top_p=-1.0, top_k=-1, min_p=-1.0, repetition_penalty=-1.0, frequency_penalty=0.0, presence_penalty=0.0, seed=-1, stop_sequences="", timeout_seconds=120, force_regenerate=False, video_mode="auto", video_frame_count=0, images=None, videos=None, audios=None, clip=None):
        if provider not in PROVIDERS and provider != "ComfyUI Local":
            raise ValueError("Choose a supported LLM provider.")
        if thinking not in ("auto", "enabled", "disabled") or reasoning_effort not in ("auto", "low", "medium", "high"):
            raise ValueError("Choose a supported thinking mode and reasoning effort.")
        if provider == "ComfyUI Local":
            return (generate_local(clip, prompt_idea, system_prompt, thinking, max_output_tokens, temperature, top_p, top_k, min_p, repetition_penalty, presence_penalty, seed, stop_sequences, images, videos, audios, video_mode, video_frame_count),)
        if not model.strip():
            raise ValueError("Enter a model name from your provider or local server.")
        endpoint = _endpoint(provider, base_url)
        env_name = api_key_env.strip() or PROVIDERS[provider][1]
        key = api_key.strip() or os.environ.get(env_name, "").strip()
        if provider in ("OpenRouter", "Cerebras", "Groq") and not key:
            raise ValueError("Set api_key or the provider's API key environment variable.")
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = "Bearer " + key
        body = _payload(provider, model.strip(), prompt_idea, system_prompt, thinking, reasoning_effort, max_output_tokens, context_window, temperature, top_p, top_k, min_p, repetition_penalty, frequency_penalty, presence_penalty, seed, stop_sequences)
        add_media(body, provider, prompt_idea, system_prompt, images, videos, audios, video_mode, video_frame_count)
        try:
            # Explicit auth prevents Requests from replacing these headers with .netrc credentials.
            response = requests.post(endpoint, headers=headers, json=body, timeout=timeout_seconds, allow_redirects=False, auth=lambda request: request)
        except requests.exceptions.Timeout:
            raise RuntimeError("LLM request timed out. Increase timeout_seconds or check the server.") from None
        except requests.exceptions.RequestException:
            raise RuntimeError("Could not reach the LLM server. Check base_url and that the server is running.") from None
        with response:
            if not 200 <= response.status_code < 300:
                hints = {
                    400: "Check the model name, thinking mode, sampling settings, and token limits.",
                    401: "Check the API key.",
                    403: "Check API key permissions and model access.",
                    404: "Check base_url and the model name.",
                    429: "Rate limit or quota reached. Wait or check your provider quota.",
                }
                hint = "Redirects are not followed. Set base_url to the final API URL." if 300 <= response.status_code < 400 else hints.get(response.status_code, "Check the LLM server and provider status.")
                if provider == "Cerebras":
                    try:
                        error_result = response.json()
                    except requests.exceptions.JSONDecodeError:
                        error_result = None
                    error = error_result.get("error") if isinstance(error_result, dict) else None
                    detail = error.get("message") if isinstance(error, dict) else error
                    if isinstance(detail, str) and detail.strip():
                        detail = " ".join(detail.split())
                        if key:
                            detail = detail.replace(key, "[redacted]")
                        hint = f"{detail[:300]} {hint}"

                raise RuntimeError(f"LLM request failed (HTTP {response.status_code}). {hint}")
            try:
                result = response.json()
            except requests.exceptions.JSONDecodeError:
                raise RuntimeError("LLM server returned invalid JSON. Check the API endpoint.") from None
        return (_answer(provider, result),)


TEXT_SETTINGS = io.Custom("GOAT_TEXT_SETTINGS")
PROMPT_INPUTS = {"prompt_idea", "system_prompt", "images", "videos", "audios", "clip"}


class GOATTextGeneratorSettings(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="GOATTextGeneratorSettings",
            display_name="GOAT'd Text Generator Settings 🐐",
            category="LLM/GOAT'd",
            description="Provider, model, and generation settings for the GOAT'd Text Generator.",
            inputs=[field for field in UniversalLLM.define_schema().inputs if field.id not in PROMPT_INPUTS],
            outputs=[TEXT_SETTINGS.Output("settings")],
        )

    @classmethod
    def fingerprint_inputs(cls, force_regenerate=False, **kwargs):
        return float("nan") if force_regenerate else ""

    @classmethod
    def execute(cls, **kwargs):
        fields = cls.define_schema().inputs
        settings = {field.id: kwargs.get(field.id, field.default) for field in fields}
        return io.NodeOutput(settings)


class GOATUniversalTextGenerator(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        fields = {field.id: field for field in UniversalLLM.define_schema().inputs}
        return io.Schema(
            node_id="GOATUniversalTextGenerator",
            display_name="GOAT'd Text Generator 🐐",
            category="LLM/GOAT'd",
            description="Turn a prompt idea into text using your selected guide and system prompt. Generated text appears on this node and remains available as a STRING output.",
            search_aliases=["universal llm", "openrouter", "cerebras", "ollama", "lm studio", "language model"],
            is_output_node=True,
            inputs=[
                fields["prompt_idea"],
                fields["system_prompt"],
                io.Combo.Input("prompt_guide", options=GUIDE_OPTIONS, default="None"),
                fields["images"],
                fields["videos"],
                fields["audios"],
                fields["clip"],
                TEXT_SETTINGS.Input("settings"),
            ],
            outputs=[io.String.Output("text", display_name="text")],
        )

    @classmethod
    def fingerprint_inputs(cls, settings=None, **kwargs):
        return float("nan") if isinstance(settings, dict) and settings.get("force_regenerate", False) else ""

    @classmethod
    def execute(cls, prompt_idea, system_prompt, prompt_guide, settings, clip=None, images=None, videos=None, audios=None):
        if not isinstance(settings, dict):
            raise ValueError("Connect GOAT'd Text Generator Settings 🐐 to settings.")
        fields = GOATTextGeneratorSettings.define_schema().inputs
        settings_fields = {field.id for field in fields}
        if set(settings) != settings_fields:
            raise ValueError("Reconnect Text Generator Settings; its settings data is incomplete or incompatible.")
        for field in fields:
            value = settings[field.id]
            default = field.default
            if isinstance(default, bool):
                valid = isinstance(value, bool)
            elif isinstance(default, int):
                valid = isinstance(value, int) and not isinstance(value, bool)
            elif isinstance(default, float):
                valid = isinstance(value, (float, int)) and not isinstance(value, bool)
            else:
                valid = isinstance(value, str)
            if not valid:
                raise ValueError(f"Settings value {field.id} has an invalid type. Reconnect Text Generator Settings.")
        text, = UniversalLLM.generate(
            **settings,
            prompt_idea=prompt_idea,
            system_prompt=combine_system_prompt(system_prompt, prompt_guide, reference_names=tuple(name for group in (images, videos, audios) for name, value in (group or {}).items() if value is not None)),
            clip=clip, images=images, videos=videos, audios=audios,
        )
        return io.NodeOutput(text, ui=ui.PreviewText(text))
