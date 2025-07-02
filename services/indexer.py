from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Iterable, List

from PIL import Image, ImageDraw, ImageFont


@dataclass
class ElementInfo:
    index: int
    tag: str
    text: str
    x: int
    y: int
    width: int
    height: int


def draw_boxes(image_bytes: bytes, elements: Iterable[ElementInfo]) -> bytes:
    """Overlay bounding boxes and indices on PNG bytes."""
    img = Image.open(io.BytesIO(image_bytes))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default()
    except Exception:
        font = None
    for el in elements:
        rect = (el.x, el.y, el.x + el.width, el.y + el.height)
        draw.rectangle(rect, outline="red", width=2)
        if font:
            draw.text((el.x, el.y), str(el.index), fill="red", font=font)
        else:
            draw.text((el.x, el.y), str(el.index), fill="red")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
