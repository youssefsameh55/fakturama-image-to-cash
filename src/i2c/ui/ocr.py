"""Screen capture + Windows OCR (Windows.Media.Ocr) returning words with screen-space boxes.

Only used for Fakturama's NatTable tables, which expose nothing to UIA (probe-confirmed).
The rectangle to read always comes from UIA - never from hardcoded coordinates.
"""
from __future__ import annotations

import asyncio
from functools import lru_cache

from PIL import Image

from i2c.errors import UIError
from i2c.ui.geom import Rect, Word


def capture(rect: Rect) -> Image.Image:
    """Screenshot of a screen rectangle (physical pixels; process must be DPI-aware)."""
    import mss  # Windows-only dependency, imported lazily so offline tests don't need it

    if rect.width <= 0 or rect.height <= 0:
        raise UIError(f"cannot capture empty rect {rect}")
    factory = getattr(mss, "MSS", None) or mss.mss
    with factory() as sct:
        shot = sct.grab({"left": rect.left, "top": rect.top, "width": rect.width, "height": rect.height})
        return Image.frombytes("RGB", shot.size, shot.rgb)


@lru_cache(maxsize=4)
def _engine(language: str):  # noqa: ANN202 - winrt type
    from winrt.windows.globalization import Language
    from winrt.windows.media.ocr import OcrEngine

    lang = Language(language)
    if not OcrEngine.is_language_supported(lang):
        raise UIError(f"Windows OCR language {language!r} not installed "
                      "(Settings > Time & language > Language > add it with 'Optical character recognition')")
    return OcrEngine.try_create_from_language(lang)


async def _recognize(engine, bitmap):  # noqa: ANN001, ANN202
    return await engine.recognize_async(bitmap)


def ocr_image(img: Image.Image, origin: tuple[int, int] = (0, 0), scale: float = 1.0,
              language: str = "en-US") -> list[Word]:
    """OCR a PIL image. Word boxes are mapped back to screen space: origin + box / scale.

    `scale` > 1 upscales first; on high-DPI screens 1 works best (measured on probe screenshots).
    """
    from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.storage.streams import DataWriter

    src = img.convert("RGBA")
    if scale != 1:
        src = src.resize((max(1, int(src.width * scale)), max(1, int(src.height * scale))),
                         Image.LANCZOS)
    writer = DataWriter()
    writer.write_bytes(src.tobytes("raw", "BGRA"))
    bitmap = SoftwareBitmap.create_copy_from_buffer(
        writer.detach_buffer(), BitmapPixelFormat.BGRA8, src.width, src.height)
    from i2c.perf import timed

    with timed("ocr_pass"):
        result = asyncio.run(_recognize(_engine(language), bitmap))

    ox, oy = origin
    words: list[Word] = []
    for line in result.lines:
        for w in line.words:
            b = w.bounding_rect
            words.append(Word(w.text, Rect(
                ox + int(b.x / scale), oy + int(b.y / scale),
                ox + int((b.x + b.width) / scale), oy + int((b.y + b.height) / scale))))
    return words


def ocr_rect(rect: Rect, scale: float = 1.0, language: str = "en-US") -> tuple[list[Word], Image.Image]:
    """Capture `rect` and OCR it. Returns (words in screen coords, the captured image)."""
    img = capture(rect)
    return ocr_image(img, (rect.left, rect.top), scale, language), img
