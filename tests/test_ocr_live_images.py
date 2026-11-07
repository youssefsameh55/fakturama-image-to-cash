"""Real Windows OCR on real Fakturama table captures (regressions found live). Skipped without
Windows OCR."""
from pathlib import Path

import pytest
from PIL import Image

from i2c.ui.geom import Rect
from i2c.ui.table import build_table, detect_vertical_lines, header_band, recover_dropped_cells

ocr = pytest.importorskip("i2c.ui.ocr")
pytest.importorskip("winrt.windows.media.ocr")
FIX = Path(__file__).parent / "fixtures" / "ocr"


def read(name: str) -> list[dict]:
    img = Image.open(FIX / name)
    r = Rect(0, 0, img.width, img.height)
    words = ocr.ocr_image(img)
    band = header_band(words, 8)
    lines = detect_vertical_lines(img, *band)
    t = build_table(words, r, lines)
    return recover_dropped_cells(t, img, r, lambda im, s: ocr.ocr_image(im, (0, 0), s), *band).as_dicts()


def test_partly_dropped_cells_vat_19():
    rows = read("vats_live.png")
    assert [(x["Name"], x["Description"], x["Value"]) for x in rows] == [
        ("Tax-free", "Free of Tax", "0.00 %"), ("VAT 19%", "VAT 19%", "19.00 %")]


def test_fully_dropped_numbers_payment():
    (row,) = read("payments_probe.png")
    assert (row["Name"], row["Description"], row["Discount"], row["Disc. Days"], row["Net Days"]) == \
        ("Cash", "Pay Cash", "0%", "0", "0")


def test_selected_row_light_header_lines_and_stroke_words():
    from i2c.calc import parse_decimal
    img = Image.open(FIX / "items_selected_live.png")
    r = Rect(0, 0, img.width, img.height)
    words = ocr.ocr_image(img)
    band = header_band(words, 8)
    lines = detect_vertical_lines(img, *band)
    assert len(lines) == 9                       # grey header, light separators
    exp = ["Pos.", "Qty.", "Item No.", "Picture", "Name", "Description", "VAT", "U.Price", "Discount", "Price"]
    (row,) = build_table(words, r, lines, exp).as_dicts()
    assert parse_decimal(row["Qty."]) == 1 and row["U.Price"] == "$250.00" and row["Price"] == "$250.00"
    assert row["Discount"] == "0.00 %" and parse_decimal(row["VAT"]) == 19
