# Author: ninjaGurung
# GitHub: https://github.com/ninjaGurung
import base64
from bisect import bisect_left
from io import BytesIO
import re
import wave

import av
import numpy as np
from PIL import Image

from comfy_api.latest import Types


_TAG = re.compile(r"(?<![\w@])@(image|video|audio)(\d+)(?!\w)")
_IMAGE_ONLY = {"Cerebras", "LM Studio", "Ollama", "Groq", "OpenCode"}


def _connected(items, prefix, maximum):
    connected = {}
    for name, value in (items or {}).items():
        if value is None:
            continue
        if name not in {f"{prefix}{i}" for i in range(1, maximum + 1)}:
            raise ValueError(f"Unknown media input {name}. Use {prefix}1 through {prefix}{maximum}.")
        connected[name] = value
    return dict(sorted(connected.items(), key=lambda item: int(item[0][len(prefix):])))


def _image_data(image):
    pixels = image.detach().cpu().float().numpy()
    pixels = np.clip(pixels * 255, 0, 255).astype(np.uint8)
    buffer = BytesIO()
    Image.fromarray(pixels).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _audio_data(waveform, sample_rate):
    samples = waveform.detach().cpu().float().numpy()
    samples = (np.clip(samples, -1, 1) * 32767).astype("<i2")
    buffer = BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(samples.shape[0])
        output.setsampwidth(2)
        output.setframerate(int(sample_rate))
        output.writeframes(samples.T.tobytes())
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _video_data(video):
    buffer = BytesIO()
    video.save_to(buffer, format=Types.VideoContainer.MP4, codec=Types.VideoCodec.H264)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _video_frames(video, count):
    duration = video.get_duration()
    frame_rate = float(video.get_frame_rate())
    if duration <= 0 or frame_rate <= 0:
        raise ValueError("Video has no readable frames.")
    video = video.as_trimmed(0.0, duration, strict_duration=False)
    if video is None:
        raise ValueError("Video has no readable frames.")
    start_time, _ = video.get_active_trim_window()
    timestamps = []
    with av.open(video.get_stream_source(), mode="r") as container:
        stream = container.streams.video[0]
        start_pts = int(start_time / stream.time_base)
        end_pts = int((start_time + duration) / stream.time_base)
        if start_pts:
            container.seek(start_pts, stream=stream)
        for frame in container.decode(stream):
            if frame.pts < start_pts:
                continue
            if frame.pts >= end_pts:
                break
            timestamps.append(float(frame.pts * stream.time_base) - start_time)
    if not timestamps:
        raise ValueError("Video has no readable frames.")
    frame_interval = 1 / frame_rate
    for index in range(count):
        target = timestamps[0] + (timestamps[-1] - timestamps[0]) * index / (count - 1) if count > 1 else timestamps[0]
        timestamp = timestamps[min(bisect_left(timestamps, target), len(timestamps) - 1)]
        segment = video.as_trimmed(timestamp, min(frame_interval, duration - timestamp), strict_duration=False)
        if segment is None:
            raise ValueError(f"Could not read video frame at {timestamp:.3f}s.")
        frames = segment.get_components().images
        if frames.shape[0] == 0:
            raise ValueError(f"Could not read video frame at {timestamp:.3f}s.")
        yield timestamp, frames[0]


def add_media(body, provider, prompt_idea, system_prompt, images=None, videos=None, audios=None, video_mode="auto", video_frame_count=0):
    images = _connected(images, "image", 10)
    videos = _connected(videos, "video", 4)
    audios = _connected(audios, "audio", 6)
    connected = images | videos | audios
    for prompt in (prompt_idea, system_prompt):
        for match in _TAG.finditer(prompt):
            tag = match.group(1) + match.group(2)
            if tag not in connected:
                raise ValueError(f"@{tag} has no connected media. Connect it or remove that reference.")
    if not connected:
        return
    if video_mode not in ("auto", "native", "frames"):
        raise ValueError("Choose auto, native, or frames for video_mode.")
    if audios and provider in _IMAGE_ONLY:
        raise ValueError(f"{provider} does not accept audio in this node. Use OpenRouter or an audio-capable OpenAI-compatible server.")
    native_video = video_mode == "native" or video_mode == "auto" and provider not in _IMAGE_ONLY
    if videos and native_video and provider in _IMAGE_ONLY:
        raise ValueError(f"{provider} does not accept native video. Choose frames for video_mode.")
    if video_frame_count == 0:
        video_frame_count = 2 if provider in ("Cerebras", "Groq") else 12
    if videos and not native_video and (not isinstance(video_frame_count, int) or video_frame_count < 1):
        raise ValueError("video_frame_count must be 0 (automatic) or a positive integer.")

    parts = [{"type": "text", "text": prompt_idea}]
    ollama_images = []
    labels = []

    def append_image(label, data):
        labels.append(label)
        parts.extend([
            {"type": "text", "text": label},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + data}},
        ])
        ollama_images.append(data)

    for name, image in images.items():
        if image.ndim != 4 or image.shape[0] == 0:
            raise ValueError(f"@{name} requires a nonempty IMAGE batch [B,H,W,C].")
        for index, frame in enumerate(image):
            label = f"@{name}" if image.shape[0] == 1 else f"@{name} image {index + 1}/{image.shape[0]}"
            append_image(label, _image_data(frame))
    for name, video in videos.items():
        if native_video:
            parts.extend([
                {"type": "text", "text": f"@{name}"},
                {"type": "video_url", "video_url": {"url": "data:video/mp4;base64," + _video_data(video)}},
            ])
        else:
            for timestamp, frame in _video_frames(video, video_frame_count):
                append_image(f"@{name} frame at {timestamp:.3f}s (visual frames only)", _image_data(frame))
    for name, audio in audios.items():
        waveform = audio["waveform"]
        if waveform.ndim != 3 or waveform.shape[0] == 0:
            raise ValueError(f"@{name} requires a nonempty AUDIO batch [B,C,T].")
        for index, member in enumerate(waveform):
            label = f"@{name}" if waveform.shape[0] == 1 else f"@{name} audio {index + 1}/{waveform.shape[0]}"
            parts.extend([
                {"type": "text", "text": label},
                {"type": "input_audio", "input_audio": {"data": _audio_data(member, audio["sample_rate"]), "format": "wav"}},
            ])

    if provider == "LM Studio":
        body["input"] = [
            {"type": "text", "content": part["text"]} if part["type"] == "text"
            else {"type": "image", "data_url": part["image_url"]["url"]}
            for part in parts
        ]
    elif provider == "Ollama":
        user = body["messages"][-1]
        user["content"] = prompt_idea + "\n\nAttachments in order:\n" + "\n".join(labels)
        user["images"] = ollama_images
    else:
        body["messages"][-1]["content"] = parts
