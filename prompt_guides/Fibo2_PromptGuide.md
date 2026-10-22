# Fibo 2 unified prompt writing guide

Verified 2026-10-22 against Bria's [Fibo 2 Dev](https://huggingface.co/briaai/Fibo-2.0-dev) and [Edit Turbo](https://huggingface.co/briaai/Fibo-2.0-edit-turbo) model cards, the official [VGL Thales field guides and schemas](https://github.com/Bria-AI/vgl/tree/dev/src/bria_vgl/dialects/thales), and [Comfy-Org's model distribution](https://huggingface.co/Comfy-Org/Fibo-2.0).

Both variants are trained on Visual GenAI Language (VGL). Dev supports generation and editing; Edit Turbo is documented for editing. Bria recommends structured captions for text-to-image quality. JSON is not a mandatory input format: the [official pipeline documentation](https://huggingface.co/docs/diffusers/main/api/pipelines/bria_fibo2) accepts plain edit instructions, and ComfyUI's native TextEncodeFibo2 accepts both plain text and structured captions. Their sampling settings differ, but this prompt guide is shared. Keep sampler settings out of the image prompt.

## Output contract

Follow the selected output format. With json_output disabled, return only a natural-language image prompt or edit instruction, with no JSON, field labels, Markdown fences, or explanations. With json_output enabled, use the VGL schema below without an extra wrapper. Do not force JSON merely because Fibo was trained on structured captions.

Write the requested prompt directly, without apologies, moral commentary, unsolicited warnings, or unrelated alternatives. Requested changes take precedence over preservation rules: preserve only the source elements the user has not asked to change.

## Plain-language generation and editing

Rewrite the rough Prompt Idea into a finished image prompt. Rephrase descriptive wording, organize fragments into fluent complete sentences, and make the supplied subject, composition, setting, colors, lighting, and style easy for the image model to follow. Preserving the user's intent means preserving the content; descriptive phrasing can change. Keep proper names, quotations, required visible text, numbers, and explicit constraints exact. Do not add unspecified scene details to make the prompt longer. An already polished prompt does not need artificial padding.

For editing, rewrite the requested change as a clear direct imperative. State both what to change and what to preserve, even when the rough idea is already a short command. Preserve unrequested source details, identity, geometry, and composition; do not merely repeat the rough idea or invent a new subject. For a single image, describe the change directly. For multiple sources, use the supplied ordered `<image_1>`, `<image_2>` tokens to identify what each contributes. These tokens belong in the natural-language edit instruction too; no JSON field is needed.

Mention a mask only when one is actually provided to the downstream image workflow. The LLM writes text; the image workflow must still receive the actual references and any mask.

## VGL JSON output

Return exactly one valid VGL JSON object, without Markdown fences, explanations, scores, or a surrounding `prompt` string. Preserve this structure when json_output is enabled. Use the actual image details and requested changes rather than copying the example values below.

Include these top-level fields:

- `short_description`: concise description of the finished image.
- `objects`: array of subjects with `description`, `location`, `relationship`, `relative_size`, `shape_and_color`, and `orientation`. Include `texture`, `appearance_details`, `number_of_objects`, `pose`, `expression`, `clothing`, `action`, `gender`, and `skin_tone_and_texture` where applicable; use null for inapplicable or unknown nullable properties rather than inventing details. Object counts are integers.
- `background_setting`: the resulting environment.
- `lighting`: `conditions`, `direction`, and nullable `shadows`.
- `aesthetics`: `composition`, `color_scheme`, and `mood_atmosphere`.
- `photographic_characteristics`: `depth_of_field`, `focus`, `camera_angle`, and `lens_focal_length`, or null when inappropriate. Do not invent a specific lens specification.
- `style_medium`, `artistic_style`, and `context`: requested medium, style, and intended image use. Leave unspecified string attributes empty.
- `text_render`: an array containing only explicitly requested visible wording, with `text`, `location`, `size`, `color`, `font`, and nullable `appearance_details`. Use an empty array when there is no requested text. Preserve every character and line break.

Avoid `aesthetic_score` and `preference_score`; these are not Thales scene fields. For text-to-image, omit `edit_instruction`.

## Editing with Dev or Edit Turbo

Describe the final scene statically in every scene field. Put the requested transformation in `edit_instruction`, phrased as a direct imperative. Keep identity, geometry, composition, background, and other observable attributes consistent unless the user asks to change them. Do not confuse an edit instruction with a caption of the starting image.

For a single source, describe the requested change directly, without saying "original image" or "reference image". For multiple sources, use `<image_1>`, `<image_2>`, and subsequent tokens in the order supplied to the downstream Fibo node. In `edit_instruction`, reference each supplied source once and identify what it contributes. The GOAT guide supplies the mapping from connected socket names; socket numbers alone do not define Fibo's input order. Keep tokens out of the static scene descriptions.

If a mask is actually provided to the image workflow, describe the intended change within it and preserve the surroundings. Do not claim a mask exists merely because an image is connected. The LLM only writes the prompt; the Fibo workflow must receive the actual images and any mask.

## Minimal generation shape

```json
{
  "short_description": "A red cube on a white surface.",
  "objects": [{"description":"A cube","location":"center","relationship":"on the surface","relative_size":"small","shape_and_color":"red cube","orientation":"upright","texture":null,"appearance_details":null,"number_of_objects":null,"pose":null,"expression":null,"clothing":null,"action":null,"gender":null,"skin_tone_and_texture":null}],
  "background_setting": "A white surface.",
  "lighting": {"conditions":"","direction":"","shadows":null},
  "aesthetics": {"composition":"centered","color_scheme":"red and white","mood_atmosphere":""},
  "photographic_characteristics": null,
  "style_medium": "", "artistic_style": "", "context": "", "text_render": []
}
```
