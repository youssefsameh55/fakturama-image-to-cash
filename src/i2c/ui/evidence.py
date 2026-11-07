"""Annotated screenshots per step (D13): artifacts/<run-id>/NN_<step>.png.

Each image = capture of the Fakturama window, target control(s) outlined, caption banner
(green = verified, red = failed / manual review).
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from i2c.ui.geom import Rect

log = logging.getLogger(__name__)

OK_COLOR = (22, 140, 60)
FAIL_COLOR = (200, 30, 30)
TARGET_COLOR = (230, 0, 120)


def _font(size: int) -> ImageFont.ImageFont:
    for name in ("segoeui.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def annotate(img: Image.Image, origin: tuple[int, int], targets: list[Rect], caption: str,
             ok: bool = True) -> Image.Image:
    """Pure: outline `targets` (screen coords) on `img` (captured at `origin`) and add a banner."""
    out = img.convert("RGB").copy()
    draw = ImageDraw.Draw(out)
    width = max(3, out.width // 600)
    ox, oy = origin
    for r in targets:
        box = r.offset(-ox, -oy)
        m = width + 2  # draw outside the target so the outline never covers its text
        draw.rectangle((box.left - m, box.top - m, box.right + m, box.bottom + m),
                       outline=TARGET_COLOR, width=width)

    size = max(18, out.width // 80)
    font = _font(size)
    banner_h = int(size * 1.8)
    banner = Image.new("RGB", (out.width, banner_h), OK_COLOR if ok else FAIL_COLOR)
    ImageDraw.Draw(banner).text((size // 2, (banner_h - size) // 2 - 2),
                                ("OK  " if ok else "FAIL  ") + caption, fill="white", font=font)
    combined = Image.new("RGB", (out.width, out.height + banner_h))
    combined.paste(banner, (0, 0))
    combined.paste(out, (0, banner_h))
    return combined


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:60] or "step"


class Evidence:
    """Numbered, annotated screenshots for one run.

    Only the capture happens on the run's path; annotating + PNG encoding of the ~6 MP image
    (measured ~0.5-1 s each, ~25 per run) runs in a background thread. `close()` waits for it.
    """

    def __init__(self, run_dir: Path, area=None, background: bool = True) -> None:  # noqa: ANN001
        """`area()` returns the Rect to capture (normally the Fakturama window)."""
        from concurrent.futures import ThreadPoolExecutor

        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.area = area
        self.counter = 0
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="evidence") if background else None

    def close(self) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=True)
            self._pool = None

    def _write(self, image: Image.Image, origin: tuple[int, int], targets: list[Rect], caption: str,
               ok: bool, path: Path) -> None:
        try:
            annotate(image, origin, targets, caption, ok).save(path, compress_level=1)
        except Exception:  # noqa: BLE001
            log.warning("could not save evidence %s", path.name, exc_info=True)

    def shot(self, step: str, caption: str, targets: list[Rect] | None = None, ok: bool = True,
             image: Image.Image | None = None, origin: tuple[int, int] | None = None) -> Path | None:
        """Capture (or use `image` taken at `origin`), annotate, save. Never raises: evidence
        must not break a run, so failures are logged and None returned."""
        self.counter += 1
        path = self.run_dir / f"{self.counter:02d}_{slug(step)}.png"
        from i2c.perf import timed

        try:
            with timed("evidence_capture"):
                if image is None:
                    from i2c.ui.ocr import capture

                    rect = self.area() if self.area else None
                    if rect is None:
                        raise ValueError("no capture area")
                    image, origin = capture(rect), (rect.left, rect.top)
            args = (image, origin or (0, 0), list(targets or []), caption, ok, path)
            if self._pool is not None:
                self._pool.submit(self._write, *args)
            else:
                self._write(*args)
            log.info("evidence %s: %s", path.name, caption)
            return path
        except Exception:  # noqa: BLE001
            log.warning("could not save evidence %s", path.name, exc_info=True)
            return None
