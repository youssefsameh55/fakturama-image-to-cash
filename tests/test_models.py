from datetime import date
from decimal import Decimal as D

import pytest
from pydantic import ValidationError

from i2c.models import OrderData, PaymentStatus


def test_golden_fixture_parses(expected_dict):
    o = OrderData.model_validate(expected_dict)
    assert o.order_date == date(2026, 7, 14)
    assert o.items[0].unit_net_price == D("250.00")
    assert o.items[0].discount_percent == D("10")
    assert o.payment.status is PaymentStatus.PAID
    assert o.totals.gross == D("678.30")
    assert not o.debtor.delivery_same_as_billing


def test_roundtrip_keeps_money_as_strings(expected_dict):
    o = OrderData.model_validate(expected_dict)
    d = o.to_json_dict()
    assert d["items"][0]["unit_net_price"] == "250.00"
    assert d["totals"]["vat"] == "108.30"
    assert OrderData.model_validate(d) == o


def test_numeric_coercion_is_lenient_but_exact(mutate):
    d = mutate(lambda d: (d["items"][0].update(unit_net_price=250.1, vat_percent="19 %",
                                                discount_percent=None),
                          d["totals"].update(net="€ 570.00")))
    o = OrderData.model_validate(d)
    assert o.items[0].unit_net_price == D("250.1")      # not 250.09999...
    assert o.items[0].vat_percent == D("19")
    assert o.items[0].discount_percent == D("0")
    assert o.totals.net == D("570.00")


def test_non_numeric_money_is_rejected(mutate):
    with pytest.raises(ValidationError):
        OrderData.model_validate(mutate(lambda d: d["totals"].update(net="five hundred")))


def test_bad_date_is_rejected(mutate):
    with pytest.raises(ValidationError):
        OrderData.model_validate(mutate(lambda d: d.update(order_date="14/07/2026")))


def test_status_normalised(mutate):
    assert OrderData.model_validate(mutate(lambda d: d["payment"].update(status="paid"))).payment.status \
        is PaymentStatus.PAID
    assert OrderData.model_validate(mutate(lambda d: d["payment"].update(status="partially"))).payment.status \
        is PaymentStatus.OTHER


def test_delivery_same_as_billing(mutate):
    def same(d):
        d["debtor"]["delivery_address"] = dict(d["debtor"]["billing_address"])
    assert OrderData.model_validate(mutate(same)).debtor.delivery_same_as_billing

    def missing(d):
        d["debtor"]["delivery_address"] = None
    assert OrderData.model_validate(mutate(missing)).debtor.delivery_same_as_billing


def test_blank_strings_become_none(mutate):
    o = OrderData.model_validate(mutate(lambda d: d.update(external_reference="   ")))
    assert o.external_reference is None
