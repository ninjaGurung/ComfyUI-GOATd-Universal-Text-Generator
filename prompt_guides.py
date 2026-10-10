# Author: ninjaGurung
# GitHub: https://github.com/ninjaGurung
from pathlib import Path


_MINIMAX_FILES = {
    "MiniMax H3 - T2V / FL2V": ("MinimaxH3_T2V_FL2V_PromptGuide.md",),
    "MiniMax H3 - Ref2V": ("MinimaxH3_T2V_FL2V_PromptGuide.md", "MinimaxH3_Ref2V_PromptGuide.md"),
    "MiniMax H3 - Singularity": ("MinimaxH3_Singularity_PromptGuide.md",),
}

# Primary sources checked 2026-10-10. These are paraphrased writing aids,
# not generation settings or claims that connected LLMs implement image/video models.
GUIDE_SOURCES = {
    "GPT Image 2.5": ("https://developers.openai.com/api/docs/guides/image-prompting",),
    "Qwen Image 2.1": ("https://qwen.ai/blog?id=qwen-image-2.1",),
    "FLUX.2 Klein 9B": ("https://help.bfl.ai/articles/7592221790-how-do-i-generate-quickly-with-flux-2-klein",),
    "Krea 2": ("https://github.com/krea-ai/krea-2/blob/main/docs/prompting.md", "https://www.krea.ai/blog/explorative-prompting-krea-2"),
    "Seedance 2.0": ("https://seed.bytedance.com/en/seedance2_0",),
    "Seedance 2.5": ("https://seed.bytedance.com/en/blog/one-take-creation-flexible-referencing-introducing-seedance-2-5",),
    "Nano Banana Pro": ("https://blog.google/products-and-platforms/products/gemini/prompting-tips-nano-banana-pro/",),
    "Wan 3.0": ("https://docs.modelstudio.console.alibabacloud.com/en/model-studio/wan3-video-generation-guide", "https://modelstudio.console.alibabacloud.com/model-releases/wan3.0-video"),
}

_GUIDES = {
    "GPT Image 2.5": """Write an image generation or editing prompt for GPT Image 2.5 Sunburst or Flare. State the intended image and use, subject, composition, medium, lighting, and constraints. Quote required visible text exactly and describe its position and typography. For edits, identify each actual reference's role and specify both the requested changes and preserved identity, geometry, layout, and details. Request photorealism or transparency only when intended. Keep complex requirements clearly organized. Do not invent references, extra text, or API settings. Return the image prompt; this node generates text for the image model.""",
    "Qwen Image 2.1": """Write an image creation or editing prompt for Qwen Image 2.1. Describe the intended subject, appearance, composition, setting, lighting, and visual style in clear natural language. For editing, identify each supplied reference and distinguish what changes from what must remain consistent, especially people and products. Specify transparent background or preserved transparency only when requested. Preserve exact visible wording and its placement. Do not invent reference images or turn image-generation instructions into a video sequence.""",
    "FLUX.2 Klein 9B": """Write descriptive, connected prose for FLUX.2 Klein 9B, beginning with the subject and action, then the setting, composition, materials, lighting, and atmosphere. Describe light sources, direction, quality, and color concretely. The model does not upsample prompts, so make the intended relationships explicit instead of relying on keyword piles. For reference-based editing, explain which supplied image contributes identity, setting, or style and which details remain unchanged. Preserve exact requested colors, including hexadecimal values, and visible wording. Do not add sampling settings to the image prompt.""",
    "Krea 2": """Write a natural-language image prompt for Krea 2. Preserve the user's creative idea and chosen medium or style. Simple prompts are valid for exploration; avoid imposing a style the user did not choose. When a specific result is requested, describe composition, subject details, materials, lighting, colors, and atmosphere in coherent prose. Put exact text intended to appear in the image in quotation marks and specify placement. Keep distinct objects and spatial relationships clear. Return the prompt rather than advice about iterating or a claim that longer prompts are always required.""",
    "Seedance 2.0": """Write a clear audiovisual video prompt for Seedance 2.0. Describe subjects, setting, lighting, performance, action progression, framing, camera movement, and intended sound. Separate spoken dialogue, voiceover, music, and physical sound effects when present. For supplied image, video, or audio references, state the role of each item: identity, scene, movement, camera behavior, voice, or sound. Preserve the user's requested continuity and distinguish a continuous take from planned cuts. Do not invent dialogue, durations, or reference assets. This is a writing aid based on the official multimodal model description, not a required vendor output schema.""",
    "Seedance 2.5": """Write an audiovisual creation, reference, or editing prompt for Seedance 2.5. Establish subject identities, scene, visual style, lighting, and reference roles before describing the action. Organize requested beats in playback order; use timestamps when the user gives timing or requests precise edits. Describe shot scale, camera transitions, subject movement, dialogue or singing, ambient sound, and music without contradictory instructions. Preserve appearance and voice across shots when requested. For video edits, state exactly what changes and what is retained. For extensions, explain how the new action continues the supplied clip. Do not invent references or silently expand a requested short clip into a longer narrative.""",
    "Nano Banana Pro": """Write an image generation or editing prompt for Nano Banana Pro (Gemini 3 Pro Image). Specify the subject, composition, action, location, and style, then relevant camera angle and lighting. For rendered text, give the exact wording, language, typography intent, and position. For diagrams or informational visuals, preserve the supplied factual constraints rather than inventing data. For multiple supplied references, identify what each contributes and which identities or brand details must remain consistent. State the desired edit and the elements to preserve. Do not claim access to live search or fresh facts unless the requesting workflow actually provides them.""",
    "Wan 3.0": """Write an audiovisual prompt for Wan 3.0. Describe subjects, scene, sequential action, framing, camera movement, lighting, and sound. Assign supplied references explicit roles using Image N, Video N, or Audio N, corresponding to @imageN, @videoN, or @audioN in the user's idea. Distinguish reference-based creation, first-frame or first/last-frame animation, editing, and extension according to the user's request. For edits, identify modifications and preserved elements; for extensions, describe continuity with the source clip. Include exact dialogue and requested music or ambience only when provided or requested. Keep timing and camera instructions mutually consistent. Do not invent asset references, API parameters, or a duration the user did not request.""",
}

GUIDE_OPTIONS = sorted(
    ["None", *_MINIMAX_FILES, *_GUIDES],
    key=lambda name: (0 if name in _MINIMAX_FILES else 1 if name.startswith("Seedance ") else 2, name.casefold()),
)


def combine_system_prompt(system_prompt, guide_name, reference_names=()):
    if guide_name == "None":
        return system_prompt
    if guide_name in _MINIMAX_FILES:
        guide_dir = Path(__file__).parent / "prompt_guides"
        guide = "\n\n".join((guide_dir / filename).read_text(encoding="utf-8-sig") for filename in _MINIMAX_FILES[guide_name])
        guide += "\n\nReference tag mapping: @imageN corresponds to <Picture N>, @videoN to <Video N>, and @audioN to <Audio N>. Preserve the numbered association and the user's stated reference purpose; do not infer first/last-frame roles from the number of images. Only use references supplied by the user; examples in this guide are examples, not attached assets."
    elif guide_name in _GUIDES:
        guide = _GUIDES[guide_name]
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
    return (
        f"{system_prompt}\n\nSelected prompt writing guide: {guide_name}\n{guide}\n\n"
        f"{references}\n"
        "Apply this guide together with the user's system instructions and prompt idea. "
        "Preserve explicitly supplied details, names, wording, and reference associations. "
        "When guide examples or defaults conflict with explicit user instructions, follow the user instructions. "
        "Return the requested prompt output, without guide explanations or source citations unless requested."
    )
