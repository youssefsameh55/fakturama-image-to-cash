import pytest

from i2c.extract.validate import errors, validate, warnings
from i2c.models import OrderData

from conftest import KNOWN_METHODS


def run(d: dict):
    return validate(OrderData.model_validate(d), known_payment_methods=KNOWN_METHODS)


def codes(issues):
    return sorted(i.code for i in issues)


def test_golden_fixture_is_clean(expected_dict):
    assert run(expected_dict) == []


@pytest.mark.parametrize("change, code", [
    (lambda d: d.update(external_reference=None), "missing_reference"),
    (lambda d: d.update(order_date=None), "missing_order_date"),
    (lambda d: d["debtor"].update(company=None), "missing_company"),
    (lambda d: d["debtor"]["billing_address"].update(zip=None), "missing_billing_address"),
    (lambda d: d["debtor"].update(billing_address=None), "missing_billing_address"),
    (lambda d: d.update(items=[]), "no_items"),
    (lambda d: d["items"][0].update(sku=None), "missing_item_field"),
    (lambda d: d["items"][1].update(vat_percent=None), "missing_item_field"),
    (lambda d: d["totals"].update(gross=None), "missing_total"),
    (lambda d: d["items"][0].update(line_net_total="500.00"), "line_net_mismatch"),
    (lambda d: d["items"][0].update(quantity="0"), "bad_quantity"),
    (lambda d: d["items"][0].update(vat_percent="190"), "bad_percent"),
    (lambda d: d["items"][0].update(discount_percent="-5"), "bad_percent"),
    (lambda d: d["totals"].update(net="580.00"), "net_total_mismatch"),
    (lambda d: d["totals"].update(vat="118.30"), "vat_total_mismatch"),
    (lambda d: d["totals"].update(gross="687.30"), "gross_total_mismatch"),
    (lambda d: d["payment"].update(payment_date=None), "paid_without_date"),
])
def test_errors(mutate, change, code):
    issues = run(mutate(change))
    assert code in codes(errors(issues)), issues


@pytest.mark.parametrize("change, code", [
    (lambda d: d["payment"].update(status="UNPAID"), "date_without_paid"),
    (lambda d: d["payment"].update(method="Cheque"), "unknown_payment_method"),
    (lambda d: d["payment"].update(method=None), "missing_payment_method"),
    (lambda d: d.update(currency="USD"), "currency"),
    (lambda d: d["items"][1].update(sku="chr-erg-01"), "duplicate_sku"),
])
def test_warnings(mutate, change, code):
    issues = run(mutate(change))
    assert code in codes(warnings(issues)), issues
    assert errors(issues) == []


def test_line_net_within_one_cent_is_accepted(mutate):
    assert run(mutate(lambda d: d["items"][0].update(line_net_total="450.01"))) == []


def test_missing_line_net_total_is_not_an_error(mutate):
    assert errors(run(mutate(lambda d: d["items"][0].update(line_net_total=None)))) == []


def test_payment_method_match_is_case_insensitive(mutate):
    assert run(mutate(lambda d: d["payment"].update(method="bank transfer"))) == []


def test_unpaid_without_date_is_clean(mutate):
    def change(d):
        d["payment"].update(status="UNPAID", payment_date=None)
    assert run(mutate(change)) == []
