# Author: ninjaGurung
# GitHub: https://github.com/ninjaGurung
import json
from math import gcd
from pathlib import Path
import re

from .custom_guides import list_guides, load_guide


_GUIDE_FILES = {
    "MiniMax H3 - T2V / FL2V": ("MinimaxH3_T2V_FL2V_PromptGuide.md",),
    "MiniMax H3 - Ref2V": ("MinimaxH3_T2V_FL2V_PromptGuide.md", "MinimaxH3_Ref2V_PromptGuide.md"),
    "MiniMax H3 - Singularity": ("MinimaxH3_Singularity_PromptGuide.md",),
    "MiniMax H3 - Pika": ("MinimaxH3_Pika_PromptGuide.md",),
    "MiniMax H3 - Image Edit": ("MinimaxH3Image_ImageEdit_PromptGuide.md",),
    "MiniMax H3 - Text 2 Image": ("MinimaxH3Image_PromptGuide.md",),
    "Qwen Image 2.1": ("QwenImage2.1_PromptGuide.md",),
    "Wan 3.0": ("Wan3.0_PromptGuide.md",),
    "Seedance 2.0": ("Seedance2.0_PromptGuide.md",),
    "Seedance 2.5": ("Seedance2.5_PromptGuide.md",),
    "FLUX.2 Klein 9B": ("Flux.2Klein9B_PromptGuide .md",),
    "Krea 2": ("Krea2_PromptGuide.md",),
    "MOSS Image": ("MossImage_PromptGuide.md",),
    "Fibo 2": ("Fibo2_PromptGuide.md",),
}

_MINIMAX_IMAGE_RATIO_FIELDS = {
    "MiniMax H3 - Image Edit": "target_aspect_ratio",
    "MiniMax H3 - Text 2 Image": "aspect_ratio",
}
_MINIMAX_H3_T2I_RATIOS = ("1:1", "5:4", "4:3", "3:2", "16:9", "21:9", "4:5", "3:4", "2:3", "9:16")

# Primary sources checked 2026-10-10. These are paraphrased writing aids,
# not generation settings or claims that connected LLMs implement image/video models.
GUIDE_SOURCES = {
    "MOSS Image": ("https://github.com/OpenMOSS/MOSS-Image/blob/main/USERGUIDE.md", "https://github.com/OpenMOSS/MOSS-Image/tree/main/skills/moss-image-prompt-enhancer"),
    "Fibo 2": ("https://huggingface.co/briaai/Fibo-2.0-dev", "https://huggingface.co/briaai/Fibo-2.0-edit-turbo", "https://github.com/Bria-AI/vgl/tree/dev/src/bria_vgl/dialects/thales"),
    "GPT Image 2.5": ("https://developers.openai.com/api/docs/guides/image-prompting",),
    "Nano Banana Pro": ("https://blog.google/products-and-platforms/products/gemini/prompting-tips-nano-banana-pro/",),
}

_GUIDES = {
    "GPT Image 2.5": """Write an image generation or editing prompt for GPT Image 2.5 Sunburst or Flare. State the intended image and use, subject, composition, medium, lighting, and constraints. Quote required visible text exactly and describe its position and typography. For edits, identify each actual reference's role and specify both the requested changes and preserved identity, geometry, layout, and details. Request photorealism or transparency only when intended. Keep complex requirements clearly organized. Do not invent references, extra text, or API settings. Return the image prompt; this node generates text for the image model.""",
    "Nano Banana Pro": """Write an image generation or editing prompt for Nano Banana Pro (Gemini 3 Pro Image). Specify the subject, composition, action, location, and style, then relevant camera angle and lighting. For rendered text, give the exact wording, language, typography intent, and position. For diagrams or informational visuals, preserve the supplied factual constraints rather than inventing data. For multiple supplied references, identify what each contributes and which identities or brand details must remain consistent. State the desired edit and the elements to preserve. Do not claim access to live search or fresh facts unless the requesting workflow actually provides them.""",
}

GUIDE_OPTIONS = sorted(
    ["None", *_GUIDE_FILES, *_GUIDES],
    key=lambda name: (0 if name.startswith("MiniMax ") else 1 if name.startswith("Seedance ") else 2, name.casefold()),
)
GUIDE_OPTIONS.remove("MiniMax H3 - Text 2 Image")
GUIDE_OPTIONS.insert(GUIDE_OPTIONS.index("MiniMax H3 - Image Edit") + 1, "MiniMax H3 - Text 2 Image")


def get_guide_options():
    return [*GUIDE_OPTIONS, *(guide["value"] for guide in list_guides()["guides"])]


def validate_guide_json(guide_name, text):
    ratio_field = _MINIMAX_IMAGE_RATIO_FIELDS.get(guide_name)
    if ratio_field is None:
        return
    answer = json.loads(text)
    if answer.keys() != {"expanded_caption", ratio_field} or any(not isinstance(value, str) for value in answer.values()):
        raise RuntimeError(f'{guide_name} JSON output requires exactly two string fields: "expanded_caption" and "{ratio_field}".')
    ratio = answer[ratio_field]
    if ratio_field == "aspect_ratio":
        if ratio not in _MINIMAX_H3_T2I_RATIOS:
            raise RuntimeError(f'{guide_name} returned an unsupported aspect_ratio. Choose one of {", ".join(_MINIMAX_H3_T2I_RATIOS)}.')
    elif not re.fullmatch(r"0*[1-9][0-9]*:0*[1-9][0-9]*", ratio):
        raise RuntimeError(f"{guide_name} target_aspect_ratio must be a positive-integer W:H ratio.")


def combine_system_prompt(system_prompt, guide_name, reference_names=(), json_output=False, *, image_shapes=None):
    if guide_name == "None":
        return system_prompt
    guide_label = guide_name
    if guide_name in _GUIDE_FILES:
        guide_dir = Path(__file__).parent / "prompt_guides"
        guide = "\n\n".join((guide_dir / filename).read_text(encoding="utf-8-sig") for filename in _GUIDE_FILES[guide_name])
        if guide_name == "MiniMax H3 - Pika":
            guide = guide.split("\n## 6. Running the examples with the CLI\n", 1)[0]
        if guide_name in _MINIMAX_IMAGE_RATIO_FIELDS:
            guide += "\n\nUse the connected still images only in their stated roles. Sampled video frames do not count as source pictures."
        elif guide_name.startswith("MiniMax "):
            guide += "\n\nReference tag mapping: @imageN corresponds to <Picture N>, @videoN to <Video N>, and @audioN to <Audio N>."
        elif guide_name == "Qwen Image 2.1":
            guide += "\n\nReference tag mapping: @imageN corresponds to <image_N>; for a single supplied image, use the input image as specified in the guide."
        elif guide_name == "FLUX.2 Klein 9B":
            guide += "\n\nReference tag mapping: @imageN corresponds to image N; for a single supplied image, use the input image as specified in the guide."
        elif guide_name == "Krea 2":
            guide += "\n\nReference tag mapping: @imageN corresponds to @reference_N for subject or structure references. Bind @style_reference to the specific supplied image used for style, keeping its original numbered socket association explicit."
        elif guide_name == "Fibo 2":
            if not json_output:
                guide = guide.split("\n## VGL JSON output\n", 1)[0]
            image_names = [name for name in reference_names if name.startswith("image")]
            guide += "\n\nFibo reference order follows the images supplied to the downstream Fibo node. Keep that order identical to the references here. "
            if len(image_names) > 1:
                guide += "Multi-reference tag mapping: " + ", ".join(f"@{name} corresponds to <image_{index}>" for index, name in enumerate(image_names, 1)) + ". Use these slot tokens in the edit instruction" + (" field edit_instruction." if json_output else ".")
        elif guide_name == "MOSS Image":
            guide += "\n\nConnected images are visual evidence for the text-writing LLM. For edits, @imageN corresponds to Picture N; preserve each supplied source/reference role. The downstream MOSS edit workflow must receive the same images separately and use the image edit LoRA."
        else:
            guide += "\n\nReference tag mapping: @imageN corresponds to @ImageN, @videoN to @VideoN, and @audioN to @AudioN."
        guide += " Preserve the numbered association and the user's stated reference purpose; do not infer first/last-frame roles from the number of images. Only use references supplied by the user; examples in this guide are examples, not attached assets."
    elif guide_name in _GUIDES:
        guide = _GUIDES[guide_name]
    elif isinstance(guide_name, str) and guide_name.startswith("Custom:"):
        guide_label, guide = load_guide(guide_name)
    else:
        raise ValueError("Unknown prompt guide. Choose an item from the prompt guide dropdown.")
    references = (
        "Available attached media inputs: " + ", ".join(reference_names) + ". Refer only to these attachments."
        if reference_names else
        "There are no attached images, videos, or audio. Write a text-to-image or text-to-video prompt. "
        "Do not include picture alignment, first/last-frame alignment, reference labels, or claims about supplied pictures. "
        "Reference-based sections and example assets in the guide do not apply unless the user explicitly requests a hypothetical reference template."
    )
    if not reference_names and guide_name in ("MiniMax H3 - T2V / FL2V", "MiniMax H3 - Ref2V"):
        references += " Use text-only T2VA: begin with integrated_multimodal_description, then overall_soundscape and non_diegetic_music. Omit the image-alignment instruction."
    mode = ""
    if guide_name in _MINIMAX_IMAGE_RATIO_FIELDS:
        if guide_name == "MiniMax H3 - Image Edit":
            image_names = sorted((name for name in reference_names if name.startswith("image")), key=lambda name: int(name[5:]))
            pictures = []
            for name in image_names:
                count, height, width = (image_shapes or {}).get(name, (1, 0, 0))
                divisor = gcd(width, height)
                ratio = f"{width // divisor}:{height // divisor}" if width and height else "unavailable"
                for index in range(count):
                    label = f"@{name}" if count == 1 else f"@{name} image {index + 1}/{count}"
                    pictures.append(f"{label} corresponds to <Picture {len(pictures) + 1}>, aspect ratio {ratio}.")
            references = "\n".join(pictures) or "No source images are connected."
            mode = "MiniMax H3 Image editing. "
        else:
            mode = "MiniMax H3 Image text-to-image. "
        if json_output:
            ratio_field = _MINIMAX_IMAGE_RATIO_FIELDS[guide_name]
            mode += (
                f'Return exactly one valid JSON object with two string keys, "expanded_caption" and "{ratio_field}". '
                f'Put the complete caption in "expanded_caption" and the chosen W:H ratio in "{ratio_field}". '
                'Escape double quotes and backslashes as required by JSON while preserving the decoded literal text. '
                'Do not output Markdown fences, reasoning, extra keys, or text outside the object.'
            )
        else:
            mode += "Return only the caption paragraph. Use the chosen aspect ratio to guide composition without a separate ratio line."
    elif guide_name == "MiniMax H3 - Pika":
        if reference_names:
            mode = (
                "Choose FL2VA only when the user explicitly assigns first-frame or last-frame roles; never infer those roles from attachment count. "
                "For FL2VA, put the requested image alignment first, then integrated_multimodal_description, overall_soundscape, non_diegetic_music. "
                "Otherwise use Ref2VA with these six fields in order: subject_definitions, summary, retention_analysis, detailed_description, overall_soundscape, non_diegetic_music. "
                "For Ref2VA use detailed_description instead of integrated_multimodal_description, define each supplied reference's role and preserve its numbered association. "
                "Sampled video frames remain observations of their <Video N>; do not create extra <Picture N> inputs from them. "
                "Use audio labels only for connected audio inputs; do not assume sampled videos provide an audio track. "
            )
        else:
            mode = "T2VA. Use these three fields in order: integrated_multimodal_description, overall_soundscape, non_diegetic_music. Omit image alignment and reference-only fields. "
        mode += "Write scene descriptions in English; preserve dialogue and on-screen text in their intended language. "
        mode += (
            "Return one JSON object using the selected mode's field names as top-level keys; put any FL2VA alignment instruction inside integrated_multimodal_description."
            if json_output else "Return the selected mode's labeled fields without Markdown fences."
        )
    elif guide_name == "MiniMax H3 - Ref2V" and reference_names:
        mode = (
            "Ref2V (full-reference mode). Return these six sections in order: "
            "subject_definitions, summary, retention_analysis, detailed_description, overall_soundscape, non_diegetic_music. "
            "Use detailed_description instead of integrated_multimodal_description. "
            "The T2VA/I2VA/FL2VA/L2VA document supplies shared shot, camera, dialogue, and sound rules only; "
            "its three-field output format and image-alignment preambles do not apply to this request. "
            "Define referenced subjects and their source assets, then describe the requested action using those labels. "
            "Sampled video frames remain observations of their <Video N>; do not create extra <Picture N> inputs from them. "
            "Use first/last-frame roles only when the user explicitly assigns them. "
        )
        mode += "Use the same six top-level keys in the JSON object, without a wrapper." if json_output else "Begin with subject_definitions: and use the six section labels, without a preamble."
    elif guide_name == "MOSS Image":
        if any(name.startswith("image") for name in reference_names):
            mode = "I2I / image editing with the MOSS edit LoRA. Unless the user explicitly requests a standalone caption or layout, begin the instruction with the exact words Change only, describe the requested change, then list what stays unchanged. Preserve only unrequested source details. Use Picture N for multiple supplied images and their stated roles."
        else:
            mode = "T2I. Write a still-image caption or the requested MOSS layout JSON. If the user explicitly requests an edit instruction or hypothetical edit template, begin it with Change only, describe the change, then state what stays unchanged; do not claim to have seen an unattached image."
        mode += " Do not add video timelines or audio fields."
    elif guide_name == "Fibo 2":
        if any(name.startswith("image") for name in reference_names):
            mode = "I2I / Ref2I. " + ("Return one final-state VGL JSON object with edit_instruction. " if json_output else "Return only plain-language edit instructions, without JSON, field labels, or Markdown fences. Rewrite the rough Prompt Idea as a clear, actionable edit instruction. ")
            mode += "Preserve unrequested source details. For one image, write the change directly without a source label; for multiple images use the supplied ordered slot mapping in the edit instruction."
        else:
            mode = "T2I with Fibo 2 Dev. " + ("Return one VGL JSON object without edit_instruction. " if json_output else "Return only plain-language image prompt text, without JSON, field labels, or Markdown fences. Rewrite the rough Prompt Idea into a cohesive, finished image prompt using its supplied details. ")
            mode += "Edit Turbo is documented for editing. Do not invent image references."
    elif guide_name in ("Qwen Image 2.1", "FLUX.2 Klein 9B", "Krea 2"):
        if any(name.startswith("image") for name in reference_names):
            mode = (
                "I2I / Ref2I. Connected images are source references, even if the idea says create or does not name a tag. "
                "Follow the guide's editing/reference rules and explicitly name the source image in the finished prompt using the guide's reference syntax. "
                "Write a generation or transformation instruction, not a standalone description of the reference. "
                "State what each image contributes and what must remain consistent. For a character sheet, explicitly derive every requested view from the source person's identity; "
                "preserve the requested panel count and angles rather than locking the source composition."
            )
        else:
            mode = "T2I. No images are connected. Follow the guide's text-to-image rules. Do not invent image references or editing sources."
    elif guide_name in ("Seedance 2.0", "Seedance 2.5", "Wan 3.0"):
        if reference_names:
            mode = (
                "Ref2V. Connected images, videos, or audio are source references. Include their explicit reference tags and roles in the finished video prompt "
                "using the guide's syntax, then describe the requested action, camera movement, and sound rather than merely captioning the media. "
                "Use I2V or FLF2V only when the user explicitly assigns first/last-frame roles; never infer those roles from attachment count. "
                "Use editing or extension rules when the user requests editing or continuing a supplied video."
            )
        else:
            mode = "T2V. No media are connected. Follow the guide's text-to-video rules. Do not invent media references or first/last-frame anchors."
    if mode:
        mode = (
            f"\nGeneration mode for this request: {mode} "
            "Return only the finished generation prompt. Do not output mode labels, guide headings, or rule explanations.\n"
        )
    preservation = "Preserve explicitly supplied details, names, wording, and reference associations. "
    if guide_name == "Fibo 2" and not json_output:
        preservation = "Preserve supplied facts, proper names, exact visible text, numbers, constraints, and reference associations. Rephrase descriptive wording to improve clarity and flow without inventing content. "
    return (
        f"{system_prompt}\n\nSelected prompt writing guide: {guide_label}\n{guide}\n\n"
        f"{references}\n{mode}"
        "Apply this guide together with the user's system instructions and prompt idea. "
        f"{preservation}"
        "When guide examples or defaults conflict with explicit user instructions, follow the user instructions. "
        "Return the requested prompt output, without guide explanations or source citations unless requested."
    )
