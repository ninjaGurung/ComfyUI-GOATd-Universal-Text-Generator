# Author: ninjaGurung
# GitHub: https://github.com/ninjaGurung
from pathlib import Path


_GUIDE_FILES = {
    "MiniMax H3 - T2V / FL2V": ("MinimaxH3_T2V_FL2V_PromptGuide.md",),
    "MiniMax H3 - Ref2V": ("MinimaxH3_T2V_FL2V_PromptGuide.md", "MinimaxH3_Ref2V_PromptGuide.md"),
    "MiniMax H3 - Singularity": ("MinimaxH3_Singularity_PromptGuide.md",),
    "Qwen Image 2.1": ("QwenImage2.1_PromptGuide.md",),
    "Wan 3.0": ("Wan3.0_PromptGuide.md",),
    "Seedance 2.0": ("Seedance2.0_PromptGuide.md",),
    "Seedance 2.5": ("Seedance2.5_PromptGuide.md",),
    "FLUX.2 Klein 9B": ("Flux.2Klein9B_PromptGuide .md",),
    "Krea 2": ("Krea2_PromptGuide.md",),
}

# Primary sources checked 2026-10-10. These are paraphrased writing aids,
# not generation settings or claims that connected LLMs implement image/video models.
GUIDE_SOURCES = {
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


def combine_system_prompt(system_prompt, guide_name, reference_names=()):
    if guide_name == "None":
        return system_prompt
    if guide_name in _GUIDE_FILES:
        guide_dir = Path(__file__).parent / "prompt_guides"
        guide = "\n\n".join((guide_dir / filename).read_text(encoding="utf-8-sig") for filename in _GUIDE_FILES[guide_name])
        if guide_name.startswith("MiniMax "):
            guide += "\n\nReference tag mapping: @imageN corresponds to <Picture N>, @videoN to <Video N>, and @audioN to <Audio N>."
        elif guide_name == "Qwen Image 2.1":
            guide += "\n\nReference tag mapping: @imageN corresponds to <image_N>; for a single supplied image, use the input image as specified in the guide."
        elif guide_name == "FLUX.2 Klein 9B":
            guide += "\n\nReference tag mapping: @imageN corresponds to image N; for a single supplied image, use the input image as specified in the guide."
        elif guide_name == "Krea 2":
            guide += "\n\nReference tag mapping: @imageN corresponds to @reference_N for subject or structure references. Bind @style_reference to the specific supplied image used for style, keeping its original numbered socket association explicit."
        else:
            guide += "\n\nReference tag mapping: @imageN corresponds to @ImageN, @videoN to @VideoN, and @audioN to @AudioN."
        guide += " Preserve the numbered association and the user's stated reference purpose; do not infer first/last-frame roles from the number of images. Only use references supplied by the user; examples in this guide are examples, not attached assets."
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
