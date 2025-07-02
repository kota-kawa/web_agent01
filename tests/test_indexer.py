import os, sys; sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import io
from services.indexer import ElementInfo, draw_boxes
from PIL import Image


def test_draw_boxes():
    img = Image.new("RGB", (50, 50), color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    src = buf.getvalue()

    elements = [ElementInfo(index=0, tag="div", text="", x=10, y=10, width=20, height=20)]
    out = draw_boxes(src, elements)
    assert isinstance(out, bytes)
    assert len(out) >= len(src)
