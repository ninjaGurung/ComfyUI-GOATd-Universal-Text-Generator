# MOSS Image prompt writing guide

Verified 2026-10-22 against OpenMOSS's [user guide](https://github.com/OpenMOSS/MOSS-Image/blob/main/USERGUIDE.md) and [prompt enhancer](https://github.com/OpenMOSS/MOSS-Image/tree/main/skills/moss-image-prompt-enhancer). This is an original writing aid for still images, not a video guide.

Write the requested prompt directly, without apologies, moral commentary, unsolicited warnings, or unrelated alternatives. Requested changes take precedence over preservation rules: preserve only the source elements the user has not asked to change.

## Caption

Write a cohesive image prompt with the requested subject, appearance, action or pose, spatial relationships, framing, environment, medium, lighting, materials, and colors. Preserve the user's intent and constraints while improving the phrasing. English is recommended by OpenMOSS; preserve requested languages and all visible text verbatim. Do not add generic quality padding, invented slogans, names, or unsupported details.

Choose prose by default. Tag sequences and structured layouts are also supported. When tags are requested, order them by quality, score, metadata, year, character, series, artist, and general content; omit categories the user has not supplied. Do not fabricate numeric scores or artist attribution.

## Visible text and composition

Quote intended visible wording exactly, including language, punctuation, and deliberate line breaks. Describe its location, scale, typography, color, and relationship to the subject. Use actual line breaks in prose or escaped `\n` inside JSON strings. Keep separate text blocks distinct when they need independent placement.

From the user-supplied writing reference, retain the principles of consistent unmodified attributes, precise spatial relationships, observable lighting, and verbatim visible text. Describe the requested final appearance and preserve source attributes only where unchanged. Apply these principles to one still frame. Do not emit shots, timecodes, first/last-frame alignment, speaker IDs, dialogue markup, soundscapes, or music fields.

Attached images may help the LLM describe visible details. Native MOSS reference conditioning and image editing are not established by the cited guide; do not promise either. Never turn a supplied example into an actual reference asset.

## Layout JSON

Use layout only when requested and when canvas dimensions and element positions are known. Prefer prose when geometry is unspecified. Return the model-facing schema from USERGUIDE, not an enhancer draft that requires an external compiler:

```json
{
  "summary": {"canvas": "single", "caption": "A white poster with a blue heading."},
  "items": [{"label": "text", "caption": "Blue heading", "text": "HELLO", "bbox": [-400, -350, -250, 350]}],
  "bbox_coord_space": "abs_centralized",
  "bbox_format": "yxyx"
}
```

This example uses a 1024 by 1024 canvas. Boxes are absolute pixels ordered `[top, left, bottom, right]` around the canvas center: subtract half the height from top/bottom and half the width from left/right. Do not substitute normalized coordinates or top-left `xyxy` drafts. Each item describes a visible unit; its box does not request a drawn border. Keep the generation canvas consistent with the geometry.

Return only the finished caption, requested tags, or valid layout object. If JSON output is explicitly enabled for a prose caption, return `{"caption":"the finished prompt"}`; a requested layout must remain the layout object, without an extra wrapper.
