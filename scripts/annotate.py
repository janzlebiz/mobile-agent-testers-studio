"""Set-of-Marks (SoM) visual annotation and structured screen metadata helper.

Draws numbered bounding boxes and tap-center markers onto a copy of a screenshot
(`<NN>_som.png`) so vision-language models (especially smaller/cheaper models)
can reference exact interactive elements, and writes `<NN>.meta.json` for
dashboard overlays and Maestro flow export.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from PIL import Image, ImageDraw


def write_screen_metadata(
    out_dir: Path,
    nn: str,
    elements: list[dict[str, Any]],
    width: int,
    height: int,
    platform: str,
    a11y_line: str | None = None,
) -> Path:
    """Persist structured screen elements and a11y diagnostics to <NN>.meta.json."""
    meta = {
        "screen": nn,
        "platform": platform,
        "width": width,
        "height": height,
        "elements": elements,
        "a11y": a11y_line or "",
    }
    target = out_dir / f"{nn}.meta.json"
    target.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def draw_som_overlay(
    image_path: Path,
    elements: list[dict[str, Any]],
    scale_x: float = 1.0,
    scale_y: float = 1.0,
    out_path: Path | None = None,
) -> Path:
    """Render numbered bounding boxes onto a copy of image_path (<NN>_som.png)."""
    img = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(img)
    palette = ["#ef4444", "#3b82f6", "#10b981", "#f59e0b", "#8b5cf6"]

    for idx, el in enumerate(elements[:40], 1):
        box = el.get("box")
        if not box or len(box) != 4:
            continue
        x1, y1, x2, y2 = (
            int(box[0] * scale_x),
            int(box[1] * scale_y),
            int(box[2] * scale_x),
            int(box[3] * scale_y),
        )
        if x2 <= x1 or y2 <= y1:
            continue
        color = palette[(idx - 1) % len(palette)]
        draw.rectangle([x1, y1, x2, y2], outline=color, width=2)
        tag = str(idx)
        tw = max(14, 7 * len(tag) + 6)
        ty1 = max(0, y1 - 14)
        draw.rectangle([x1, ty1, x1 + tw, ty1 + 14], fill=color)
        draw.text((x1 + 3, ty1 + 2), tag, fill="#ffffff")

    target = out_path or image_path.with_name(f"{image_path.stem}_som.png")
    img.save(target)
    return target
