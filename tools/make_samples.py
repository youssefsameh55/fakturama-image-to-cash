"""Render synthetic order images in the same layout as samples/order_01.png.

    python tools/make_samples.py            -> samples/order_02.png, samples/order_03.png

Totals are computed here with Decimal / ROUND_HALF_UP, so every image is internally consistent.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

D = Decimal
Q = D("0.01")
FONTS = Path("C:/Windows/Fonts")
NAVY, BLUE, GREY, TEXT = "#132b45", "#2f6fb0", "#5c6670", "#1d2733"
PANEL, LINE, ZEBRA = "#eaf0f6", "#c9d3dd", "#f5f7fa"
W, H, M = 1489, 2048, 94


def font(size: int, bold: bool = False, semi: bool = False) -> ImageFont.FreeTypeFont:
    name = "segoeuib.ttf" if bold else "seguisb.ttf" if semi else "segoeui.ttf"
    p = FONTS / name
    return ImageFont.truetype(str(p if p.exists() else FONTS / "segoeuib.ttf"), size)


def money(x: Decimal) -> str:
    return f"{x.quantize(Q, ROUND_HALF_UP):.2f}"


def render(o: dict, out: Path) -> None:
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)

    def label(x, y, s):
        d.text((x, y), s.upper(), font=font(17, semi=True), fill=GREY)

    def value(x, y, s, bold=False, size=26):
        d.text((x, y), s, font=font(size, bold=bold), fill=TEXT)

    def section(y, s):
        d.text((M, y), s, font=font(24, bold=True), fill=BLUE)

    def box(y0, y1, fill="white"):
        d.rounded_rectangle((M, y0, W - M, y1), radius=12, fill=fill, outline=LINE, width=2)

    # header
    d.rectangle((0, 0, W, 112), fill=NAVY)
    d.text((M, 40), "TJM Labs  |  Automation Assessment", font=font(24, bold=True), fill="white")
    t = "SYNTHETIC TEST DATA"
    d.text((W - M - d.textlength(t, font=font(19, bold=True)), 45), t, font=font(19, bold=True), fill="#c8d6e5")
    d.text((M, 160), "SALES ORDER INPUT", font=font(56, bold=True), fill=NAVY)
    d.text((M, 232), "Source document - extract all transaction values from this image", font=font(22), fill=GREY)
    paid = o["status"] == "PAID"
    badge_fill, badge_fg = ("#e3f3e8", "#1f6b3a") if paid else ("#fdecea", "#a3312a")
    d.rounded_rectangle((1234, 160, W - M, 208), radius=24, fill=badge_fill, outline=badge_fg, width=1)
    bw = d.textlength(o["status"], font=font(22, bold=True))
    d.text((1234 + (W - M - 1234 - bw) / 2, 171), o["status"], font=font(22, bold=True), fill=badge_fg)

    # order
    section(290, "ORDER")
    box(322, 470, PANEL)
    for x, lab, val, b in ((121, "External reference", o["ref"], True), (484, "Order date", o["date"], False),
                           (800, "Customer ID", o["cust_id"], False), (1080, "Currency", "EUR", True)):
        label(x, 352, lab)
        value(x, 380, val, bold=b)

    # customer
    section(513, "CUSTOMER AND CONTACT")
    box(546, 768)
    d.line((746, 570, 746, 745), fill=LINE, width=2)
    label(121, 576, "Company"); value(121, 604, o["company"], bold=True)
    label(121, 660, "Customer alias"); value(121, 688, o["alias"])
    label(782, 567, "Contact name"); value(782, 596, o["contact"])
    label(782, 637, "Email"); value(782, 666, o["email"])
    label(782, 707, "Phone"); value(782, 735, o["phone"])

    # addresses
    section(811, "ADDRESSES")
    box(844, 1112)
    d.line((746, 868, 746, 1090), fill=LINE, width=2)
    for x, lab, lines in ((121, "Billing address", o["billing"]), (782, "Delivery address", o["delivery"])):
        label(x, 874, lab)
        for i, ln in enumerate(lines):
            value(x, 914 + 35 * i, ln, size=24)

    # payment
    section(1155, "PAYMENT")
    box(1188, 1327, PANEL)
    label(121, 1214, "Payment method"); value(121, 1242, o["method"], bold=True)
    label(666, 1214, "Paid status"); value(666, 1242, o["status"], bold=True)
    label(987, 1214, "Payment date"); value(987, 1242, o.get("paid_on") or "-")

    # items
    section(1369, "ITEMS")
    cols = [94, 145, 341, 722, 792, 876, 1025, 1132, 1216, 1396]
    heads = ["#", "SKU", "Description", "Qty", "Unit", "Unit net\n(EUR)", "Disc.", "VAT", "Line net\n(EUR)"]
    top, hh, rh, nrows = 1402, 60, 79, 4
    d.rectangle((M, top, W - M - 1, top + hh), fill=NAVY)
    hf = font(17, bold=True)
    for i, h in enumerate(heads):
        cx = (cols[i] + cols[i + 1]) / 2
        d.multiline_text((cx, top + hh / 2), h, font=hf, fill="white", anchor="mm", align="center", spacing=-2)
    net_by_vat: dict[Decimal, Decimal] = {}
    net_total = D("0")
    for r in range(nrows):
        y0 = top + hh + r * rh
        if r % 2:
            d.rectangle((M, y0, W - M - 1, y0 + rh), fill=ZEBRA)
        if r >= len(o["items"]):
            continue
        sku, desc, qty, unit, price, disc, vat = o["items"][r]
        line = (D(qty) * D(price) * (1 - D(disc) / 100)).quantize(Q, ROUND_HALF_UP)
        net_total += line
        net_by_vat[D(vat)] = net_by_vat.get(D(vat), D("0")) + line
        cells = [str(r + 1), sku, desc, qty, unit, money(D(price)), f"{disc}%", f"{vat}%", money(line)]
        for i, c in enumerate(cells):
            f = font(20, bold=i in (1, 8))
            cy = y0 + rh / 2
            if i == 2:
                d.text((cols[i] + 11, cy), c, font=f, fill=TEXT, anchor="lm")
            else:
                d.text(((cols[i] + cols[i + 1]) / 2, cy), c, font=f, fill=TEXT, anchor="mm")
    bottom = top + hh + nrows * rh
    d.rectangle((M, top, W - M - 1, bottom), outline=LINE, width=2)
    for x in cols[1:-1]:
        d.line((x, top, x, bottom), fill=LINE, width=1)

    vat_total = sum(((n * v / 100).quantize(Q, ROUND_HALF_UP) for v, n in net_by_vat.items()), D("0"))
    gross = net_total + vat_total

    # totals
    box(1802, 1935, "#f3f6f9")
    thirds = [M, M + (W - 2 * M) / 3, M + 2 * (W - 2 * M) / 3, W - M]
    for x in thirds[1:3]:
        d.line((x, 1822, x, 1915), fill=LINE, width=1)
    for i, (lab, amt) in enumerate((("NET TOTAL", net_total), ("VAT TOTAL", vat_total), ("GROSS TOTAL", gross))):
        cx = (thirds[i] + thirds[i + 1]) / 2
        d.text((cx, 1838), lab, font=font(18, semi=True), fill=BLUE if i == 2 else GREY, anchor="mm")
        d.text((cx, 1890), f"EUR {money(amt)}", font=font(30, bold=True), fill=TEXT, anchor="mm")

    # footer
    d.line((M, 1978, W - M, 1978), fill=LINE, width=1)
    d.text((M, 2000), "All data is synthetic  |  Prices are net  |  Dates: YYYY-MM-DD  |  Currency: EUR",
           font=font(17), fill=GREY)
    d.text((W - M, 2000), o["ref"], font=font(17), fill=GREY, anchor="ra")

    img.save(out)
    print(f"{out}: net {money(net_total)}  vat {money(vat_total)}  gross {money(gross)}")


# order_02: brand-new debtor, all-new products, a 7 % VAT rate and a new payment method -> create paths.
ORDER_02 = dict(
    ref="WEB-2026-0821-B04", date="2026-08-21", cust_id="CUST-1012", status="PAID",
    company="Bluewave Studio GmbH", alias="BLUEWAVE-HAMBURG",
    contact="Jonas Weber", email="jonas.weber@example.test", phone="+49 40 5550 2384",
    billing=["Bluewave Studio GmbH", "Hafenstrasse 12", "20359 Hamburg", "Germany"],
    delivery=["Bluewave Studio GmbH", "Hafenstrasse 12", "20359 Hamburg", "Germany"],
    method="Credit Card", paid_on="2026-08-23",
    items=[("BK-UX-101", "UX Design Handbook", "4", "pcs", "34.90", "0", "7"),
           ("LMP-LED-05", "LED Desk Lamp", "2", "pcs", "59.00", "5", "19"),
           ("CBL-USBC-2M", "USB-C Cable 2 m", "10", "pcs", "8.50", "0", "19")],
)

# order_03: existing debtor (Northstar) + two existing products + one new product, UNPAID -> select paths.
ORDER_03 = dict(
    ref="WEB-2026-0905-C22", date="2026-09-05", cust_id="CUST-1007", status="UNPAID",
    company="Northstar Office GmbH", alias="NORTHSTAR-BERLIN",
    contact="Marta Klein", email="marta.klein@example.test", phone="+49 30 5550 1420",
    billing=["Northstar Office GmbH", "Friedrichstrasse 88", "10117 Berlin", "Germany"],
    delivery=["Northstar Office Warehouse", "Beusselstrasse 44", "10553 Berlin", "Germany"],
    method="Bank Transfer", paid_on=None,
    items=[("CHR-ERG-01", "Ergonomic Desk Chair", "1", "pcs", "250.00", "0", "19"),
           ("MON-27-4K", "27-inch 4K Monitor", "2", "pcs", "329.00", "10", "19"),
           ("MAT-DESK-02", "Anti-Fatigue Desk Mat", "1", "pcs", "40.00", "0", "19")],
)

if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1] / "samples"
    render(ORDER_02, root / "order_02.png")
    render(ORDER_03, root / "order_03.png")
