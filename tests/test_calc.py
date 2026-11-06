from decimal import Decimal as D

from i2c import calc


def test_q2_rounds_half_up():
    assert calc.q2(D("0.005")) == D("0.01")
    assert calc.q2(D("2.675")) == D("2.68")       # float would give 2.67
    assert calc.q2(D("-0.005")) == D("-0.01")
    assert calc.q2(D("1.004")) == D("1.00")


def test_line_net_sample_lines():
    assert calc.line_net(D("2"), D("250.00"), D("10")) == D("450.00")
    assert calc.line_net(D("3"), D("40.00"), D("0")) == D("120.00")


def test_line_net_default_discount_and_rounding():
    assert calc.line_net(D("3"), D("0.335")) == D("1.01")       # 1.005 -> 1.01
    assert calc.line_net(D("1"), D("9.99"), D("33.333")) == D("6.66")


def test_product_gross_price_ignores_discount():
    assert calc.product_gross_price(D("250.00"), D("19")) == D("297.50")
    assert calc.product_gross_price(D("40.00"), D("19")) == D("47.60")
    assert calc.product_gross_price(D("10.00"), D("7")) == D("10.70")


def test_vat_total_and_gross():
    vat = calc.vat_total([(D("450.00"), D("19")), (D("120.00"), D("19"))])
    assert vat == D("108.30")
    assert calc.gross(D("570.00"), vat) == D("678.30")


def test_vat_total_mixed_rates():
    assert calc.vat_total([(D("100.00"), D("19")), (D("100.00"), D("7"))]) == D("26.00")


def test_close():
    assert calc.close(D("1.00"), D("1.01"))
    assert not calc.close(D("1.00"), D("1.02"))
    assert calc.close(D("1.00"), D("1.03"), D("0.03"))


def test_results_are_decimal_not_float():
    assert isinstance(calc.line_net(D("2"), D("250.00"), D("10")), D)
