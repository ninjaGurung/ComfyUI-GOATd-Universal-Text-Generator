# Author: ninjaGurung
# GitHub: https://github.com/ninjaGurung
import random

import torch
import torch.nn.functional as F

import comfy.audio

from .media import _connected, _TAG, _video_frame_counts, _video_frames
from .stop_sequences import parse_stop_sequences


def _media(prompt, system_prompt, images, videos, audios, video_mode, video_frame_count):
    images = _connected(images, "image", 10)
    videos = _connected(videos, "video", 4)
    audios = _connected(audios, "audio", 6)
    connected = images | videos | audios
    for text in (prompt, system_prompt):
        for match in _TAG.finditer(text):
            name = match.group(1) + match.group(2)
            if name not in connected:
                raise ValueError(f"@{name} has no connected media. Connect it or remove that reference.")
    if videos and video_mode not in ("auto", "frames"):
        raise ValueError("ComfyUI Local reads video as visual frames. Choose auto or frames for video_mode.")
    if videos and (not isinstance(video_frame_count, int) or video_frame_count < 0):
        raise ValueError("video_frame_count must be 0 (automatic) or a positive integer.")

    frames = []
    labels = []
    for name, image in images.items():
        if image.ndim != 4 or image.shape[0] == 0:
            raise ValueError(f"@{name} requires a nonempty IMAGE batch [B,H,W,C].")
        for index, frame in enumerate(image):
            frames.append(frame)
            labels.append(f"@{name}" if image.shape[0] == 1 else f"@{name} image {index + 1}/{image.shape[0]}")
    frame_counts = _video_frame_counts(images, videos, video_frame_count)
    for name, video in videos.items():
        for timestamp, frame in _video_frames(video, frame_counts[name], unique=video_frame_count == 0):
            frames.append(frame)
            labels.append(f"@{name} frame at {timestamp:.3f}s (visual frames only)")

    image_batch = None
    if frames:
        height = max(frame.shape[0] for frame in frames)
        width = max(frame.shape[1] for frame in frames)
        first = frames[0]
        padded = [F.pad(frame.to(device=first.device), (0, 0, 0, width - frame.shape[1], 0, height - frame.shape[0])) for frame in frames]
        image_batch = torch.stack(padded)

    audio_parts = []
    audio_labels = []
    offset = 0
    for name, audio in audios.items():
        waveform = audio["waveform"]
        if waveform.ndim != 3 or waveform.shape[0] == 0 or waveform.shape[1] == 0 or waveform.shape[2] == 0:
            raise ValueError(f"@{name} requires a nonempty AUDIO batch [B,C,T].")
        waveform = comfy.audio.resample(waveform.detach().cpu().float().mean(dim=1, keepdim=True), audio["sample_rate"], 16000)
        for index, member in enumerate(waveform):
            if audio_parts:
                member = member.to(audio_parts[0])
            audio_parts.append(member)
            label = f"@{name}" if waveform.shape[0] == 1 else f"@{name} audio {index + 1}/{waveform.shape[0]}"
            end = offset + member.shape[-1]
            audio_labels.append(f"{label} at {offset / 16000:.3f}s to {end / 16000:.3f}s")
            offset = end
    audio_batch = {"waveform": torch.cat(audio_parts, dim=-1).unsqueeze(0), "sample_rate": 16000} if audio_parts else None
    if labels:
        prompt += "\n\nImages in order:\n" + "\n".join(labels)
    if audio_labels:
        prompt += "\n\nAudio segments in order:\n" + "\n".join(audio_labels)
    return prompt, image_batch, audio_batch, [frame.unsqueeze(0) for frame in frames]


def _embedding_count(tokens, kind):
    return max((sum(isinstance(token[0], dict) and token[0].get("type") == kind for batch in batches for token in batch) for batches in tokens.values()), default=0)


def generate_local(clip, prompt_idea, system_prompt, thinking, max_output_tokens, temperature, top_p, top_k, min_p, repetition_penalty, presence_penalty, seed, stop_sequences, images=None, videos=None, audios=None, video_mode="auto", video_frame_count=0):
    if clip is None:
        raise ValueError("Connect a generation-capable model from Load CLIP to the clip input for ComfyUI Local.")
    stops = parse_stop_sequences(stop_sequences)
    prompt, image, audio, source_images = _media(prompt_idea, system_prompt, images, videos, audios, video_mode, video_frame_count)
    # Qwen accepts original image sizes; Gemma uses the padded batch.
    tokenizer_options = {} if thinking == "auto" else {"thinking": thinking == "enabled"}
    tokens = clip.tokenize(prompt, image=image, images=source_images, audio=audio, video=None, skip_template=False, min_length=1, system_prompt=system_prompt, **tokenizer_options)
    if {"qwen3_4b", "qwen3_8b"}.intersection(tokens) and thinking in ("enabled", "disabled"):
        template = "<|im_start|>user\n{}<|im_end|>\n<|im_start|>assistant\n"
        if thinking == "disabled":
            template += "<think>\n\n</think>\n\n"
        tokens = clip.tokenize(prompt, image=image, images=source_images, audio=audio, video=None, skip_template=False, min_length=1, thinking=thinking == "enabled", system_prompt=system_prompt, llama_template=template)
    if "qwen3vl_32b" in tokens:
        raise ValueError("This MiniMax Qwen3-VL 32B text encoder is truncated (50 of 64 layers, no final norm/output head) and cannot faithfully generate text. Load a complete generation checkpoint instead.")
    if set(tokens).issubset({"l", "g", "clip_l", "clip_g", "t5xxl", "t5"}):
        raise ValueError("This CLIP/T5 encoder only produces embeddings. Load a supported Gemma/Qwen language model to generate text.")
    if image is not None and _embedding_count(tokens, "image") != image.shape[0]:
        raise ValueError("The loaded CLIP tokenizer did not accept every image/video frame. Load a generation-capable vision model and use its matching CLIP loader type.")
    if audio is not None and _embedding_count(tokens, "audio") == 0:
        raise ValueError("The loaded CLIP tokenizer does not accept audio. Load an audio-capable generation model or disconnect audio inputs.")
    if audio is not None:
        for batches in tokens.values():
            for batch in batches:
                for token in batch:
                    embed = token[0]
                    if isinstance(embed, dict) and embed.get("type") == "audio":
                        data = embed["data"]
                        if data.shape[-1] == 640 and data.shape[-2] * 640 < audio["waveform"].shape[-1]:
                            raise ValueError("This model accepts up to 30 seconds of combined audio. Use shorter clips or an audio-capable provider with a higher limit.")
    if {"qwen3_4b", "qwen3_8b"}.intersection(tokens) and clip.is_dynamic():
        # Native legacy casting avoids the FP8 activation scales used by dynamic generation.
        clip = clip.clone(disable_dynamic=True)
        clip.cond_stage_model = clip.patcher.model
    try:
        generated_ids = clip.generate(
            tokens,
            do_sample=temperature != 0,
            max_length=max_output_tokens,
            temperature=temperature if temperature > 0 else 1.0,
            top_k=top_k if top_k >= 0 else 50,
            top_p=top_p if top_p >= 0 else 1.0,
            min_p=min_p if min_p >= 0 else 0.0,
            repetition_penalty=repetition_penalty if repetition_penalty >= 0 else 1.0,
            presence_penalty=presence_penalty,
            seed=seed if seed >= 0 else random.getrandbits(63),
            mtp=True,
        )
    except AttributeError as error:
        if error.name != "generate":
            raise
        raise ValueError("This CLIP model cannot generate text. Load a supported Gemma/Qwen language model; ordinary CLIP and T5 encoders only produce embeddings.") from error
    generated_text = clip.decode(generated_ids)
    text = generated_text
    if generated_text.lstrip().startswith("<think>") or prompt.rstrip().endswith("<think>"):
        _, closed, text = generated_text.partition("</think>")
        if not closed:
            raise RuntimeError("The local model stopped during reasoning without an answer. Increase max_output_tokens or set thinking to disabled.")
    endings = [text.find(stop) for stop in stops if stop in text]
    if endings:
        text = text[:min(endings)]
    if not text.strip():
        raise RuntimeError("The local model produced no answer text. Increase max_output_tokens, disable thinking, or check stop_sequences.")
    return text.strip()
