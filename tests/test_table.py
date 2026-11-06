"""Pure table assembly: row grouping, grid-line columns, header matching, misreads."""
from PIL import Image, ImageDraw

from i2c.ui.geom import Rect, Word
from i2c.ui.table import (build_table, clean_text, columns_from_header_words, detect_vertical_lines,
                          group_rows, header_band, match_headers, merge_close, recover_dropped_cells)


def w(text, left, top, width=None, height=20):
    width = width or 10 * len(text)
    return Word(text, Rect(left, top, left + width, top + height))


TABLE = Rect(1000, 500, 1600, 900)


def test_group_rows_tolerates_jitter_and_sorts_by_x():
    rows = group_rows([w("b", 200, 103), w("a", 100, 100), w("c", 100, 140)], tolerance=8)
    assert [[x.text for x in r] for r in rows] == [["a", "b"], ["c"]]


def test_merge_close_collapses_3d_separator_triples():
    assert merge_close([76, 78, 80, 276, 278, 280, 500]) == [78, 278, 500]


def test_detect_vertical_lines_on_synthetic_header():
    img = Image.new("L", (600, 60), 235)
    d = ImageDraw.Draw(img)
    for x in (150, 151, 400):
        d.line([(x, 0), (x, 59)], fill=180)
    d.text((20, 20), "Item No.", fill=0)       # text must not produce lines
    d.text((170, 20), "Illl", fill=0)
    assert detect_vertical_lines(img, 0, 60) == [150, 400] or detect_vertical_lines(img, 0, 60) == [151, 400]


def test_build_table_with_grid_lines():
    words = [w("No.", 1010, 510), w("Company", 1210, 510), w("City", 1410, 510),
             w("1", 1010, 550), w("ACME", 1210, 550), w("GmbH", 1260, 550), w("Berlin", 1410, 550),
             w("2", 1010, 590), w("Other", 1210, 590), w("Paris", 1410, 590)]
    t = build_table(words, TABLE, lines=[1200, 1400], expected_headers=["No.", "Company", "City"])
    assert t.headers == ["No.", "Company", "City"]
    assert t.as_dicts() == [{"No.": "1", "Company": "ACME GmbH", "City": "Berlin"},
                            {"No.": "2", "Company": "Other", "City": "Paris"}]
    row = t.rows[0]
    assert row.rect.top == 550 and row.rect.left == TABLE.left
    assert row.click_point() == w("1", 1010, 550).rect.center


def test_long_text_stays_in_its_column_with_grid_lines():
    # A long description ends just before the next grid line; header centres would misplace it.
    words = [w("Description", 1010, 510), w("Stock", 1410, 510),
             w("A very long product description", 1010, 550, width=385), w("5", 1410, 550)]
    t = build_table(words, TABLE, lines=[1400])
    assert t.rows[0].cells == {"Description": "A very long product description", "Stock": "5"}


def test_header_gap_fallback_without_lines():
    header = [w("Item", 1010, 510), w("No.", 1060, 510), w("Name", 1210, 510)]
    cols = columns_from_header_words(header, TABLE.left, TABLE.right)
    assert len(cols) == 2 and cols[0][0] == TABLE.left and cols[-1][1] == TABLE.right
    t = build_table(header + [w("SKU-1", 1010, 550), w("Widget", 1210, 550)], TABLE, lines=None)
    assert t.as_dicts() == [{"Item No.": "SKU-1", "Name": "Widget"}]


def test_match_headers_fuzzy_without_stealing():
    raw = ["", "No.", "First Name", "Name", "Com an", "ZIP", "Cit", "address e", "additi"]
    exp = ["No.", "First Name", "Name", "Company", "ZIP", "City", "address type"]
    assert match_headers(raw, exp) == ["", "No.", "First Name", "Name", "Company", "ZIP", "City",
                                       "address type", "additi"]


def test_clean_text_repairs_percent_and_spaces():
    assert clean_text("0.00  0/0") == "0.00 %"
    assert clean_text("19.00 0/0") == "19.00 %"
    assert clean_text("  Free   of Tax ") == "Free of Tax"
    assert clean_text("10/04/2026") == "10/04/2026"


def test_empty_table_has_headers_only():
    t = build_table([w("Document", 1010, 510), w("Date", 1210, 510)], TABLE, lines=[1200])
    assert t.headers == ["Document", "Date"] and t.rows == []
    assert header_band([w("Document", 1010, 510)], 8) == (504, 536)


def test_recover_dropped_cells_uses_header_context():
    # Synthetic capture: header ink in both columns, row ink in both, but OCR "dropped" Qty.
    rect = Rect(0, 0, 400, 100)
    img = Image.new("L", (400, 100), 255)
    d = ImageDraw.Draw(img)
    d.rectangle((10, 10, 60, 25), fill=0)     # header "Name"
    d.rectangle((210, 10, 250, 25), fill=0)   # header "Qty."
    d.rectangle((10, 50, 80, 65), fill=0)     # cell "Widget"
    d.rectangle((370, 50, 380, 65), fill=0)   # cell "2" (right-aligned, dropped by OCR)
    words = [w("Name", 10, 10, 50, 15), w("Qty.", 210, 10, 40, 15), w("Widget", 10, 50, 70, 15)]
    table = build_table(words, rect, lines=[200])
    assert table.rows[0].cells == {"Name": "Widget", "Qty.": ""}

    seen = []

    def fake_ocr(image, scale):
        seen.append(image.size)
        hw = 41  # header ink width; the composite has header at x=20, cell after a 20 px gap
        return [w("Qty.", 20, 15, hw, 15), w("O", 20 + hw + 20, 15, 11, 15)]

    fixed = recover_dropped_cells(table, img, rect, fake_ocr, header_top=5, header_bottom=30)
    assert fixed.rows[0].cells == {"Name": "Widget", "Qty.": "0"}   # lone 'O' -> zero
    assert len(seen) == 1                                            # only the empty inked cell


def test_signature_changes_with_content():
    a = build_table([w("Name", 1010, 510), w("x", 1010, 550)], TABLE, lines=[])
    b = build_table([w("Name", 1010, 510), w("y", 1010, 550)], TABLE, lines=[])
    assert a.signature() != b.signature()
