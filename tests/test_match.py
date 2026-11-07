from i2c.match import Outcome, match_by_name, match_debtor, match_product, norm
from i2c.models import Address, Debtor

DEBTOR = Debtor(company="Northstar Office GmbH", first_name="Marta", last_name="Klein",
                billing_address=Address(street="Friedrichstrasse 88", zip="10117", city="Berlin"))


def row(company="Northstar Office GmbH", first="Marta", last="Klein", zip_="10117", city="Berlin"):
    return {"No.": "1", "First Name": first, "Name": last, "Company": company, "ZIP": zip_, "City": city}


def test_norm():
    assert norm("  Northstar Office   GMBH ") == "northstar office gmbh"


def test_debtor_none_exact_ambiguous_conflict():
    assert match_debtor([], DEBTOR).outcome is Outcome.NONE
    assert match_debtor([row(company="Other AG")], DEBTOR).outcome is Outcome.NONE

    r = match_debtor([row(company="Other AG"), row(company="northstar office gmbh")], DEBTOR)
    assert r.outcome is Outcome.EXACT and r.index == 1

    assert match_debtor([row(), row()], DEBTOR).outcome is Outcome.AMBIGUOUS

    c = match_debtor([row(zip_="10553")], DEBTOR)
    assert c.outcome is Outcome.CONFLICT and "ZIP" in c.reason

    # one exact + one conflicting row with the same company -> the exact one wins
    assert match_debtor([row(zip_="99999"), row()], DEBTOR).index == 1


def test_match_by_name_with_definition():
    rows = [{"Name": "Cash", "Description": "Pay Cash"}, {"Name": "Bank Transfer", "Description": "Bank Transfer"}]
    assert match_by_name(rows, "bank transfer", {"Description": "Bank Transfer"}).index == 1
    assert match_by_name(rows, "SEPA").outcome is Outcome.NONE
    c = match_by_name(rows, "Cash", {"Description": "Cash"})
    assert c.outcome is Outcome.CONFLICT and "Description" in c.reason
    assert match_by_name(rows + rows[:1], "Cash").outcome is Outcome.AMBIGUOUS


def test_vat_conflict_on_value():
    rows = [{"Name": "VAT 19%", "Value": "16.00 %"}]
    assert match_by_name(rows, "VAT 19%", {"Value": "19.00 %"}).outcome is Outcome.CONFLICT


def test_match_product():
    rows = [{"Item No.": "CHR-ERG-01", "Name": "Ergonomic Desk Chair"},
            {"Item No.": "MAT-DESK-02", "Name": "Desk Mat"}]
    assert match_product(rows, "CHR-ERG-01", "Ergonomic Desk Chair").reason == "item number equal"
    r = match_product(rows, "MAT-DESK-02", "Anti-Fatigue Desk Mat")
    assert r.outcome is Outcome.EXACT and "description differs" in r.reason
    assert match_product(rows + rows[:1], "CHR-ERG-01").outcome is Outcome.AMBIGUOUS
    assert match_product(rows, "NOPE").outcome is Outcome.NONE


def test_truncated_cells():
    from i2c.match import cell_matches
    assert cell_matches("Northstar Offic...", "Northstar Office GmbH")
    assert cell_matches("Northstar Office Gm\u2026", "Northstar Office GmbH")
    assert not cell_matches("Northstar Offic...", "Southstar Office GmbH")
    assert not cell_matches("Nor...", "Northstar Office GmbH")          # too little visible
    r = match_debtor([row(company="Northstar Offic...")], DEBTOR)
    assert r.outcome is Outcome.EXACT
    two = match_debtor([row(company="Northstar Offic..."), row(company="Northstar Office GmbH")], DEBTOR)
    assert two.outcome is Outcome.AMBIGUOUS


def test_truncation_junk_from_ocr():
    from i2c.match import cell_matches
    assert cell_matches("Northstar Office . „", "Northstar Office GmbH")   # seen live
    assert cell_matches("Northstar Office GmbH.", "Northstar Office GmbH")
    assert not cell_matches("Northstar Office", "Northstar Office GmbH")   # no truncation mark
    assert not cell_matches("Acme . „", "Northstar Office GmbH")


def test_order_address_box_check():
    import pytest
    from i2c.errors import UIError
    from i2c.flow.debtor import _expect_address
    box = "Northstar Office GmbH\r\nMarta Klein\r\nBeusselstrasse 44\r\nDE-10553 Berlin\r\nGermany"  # live
    delivery = Address(name="Northstar Office Warehouse", street="Beusselstrasse 44", zip="10553",
                       city="Berlin", country="Germany")
    _expect_address(box, delivery, "Northstar Office GmbH", "delivery")
    with pytest.raises(UIError):
        _expect_address(box, DEBTOR.billing_address, "Northstar Office GmbH", "invoice")  # wrong street/ZIP


def test_sku_ocr_lookalikes():
    from i2c.match import code_matches
    assert code_matches("CHR-ERG-OI", "CHR-ERG-01")            # seen live
    assert code_matches("MAT-DESK-O2", "MAT-DESK-02")
    assert not code_matches("CHR-ERG-02", "CHR-ERG-01")
    assert not code_matches("", "CHR-ERG-01")
    rows = [{"Item No.": "CHR-ERG-OI", "Name": "Ergonomic Des..."}]
    assert match_product(rows, "CHR-ERG-01", "Ergonomic Desk Chair").outcome is Outcome.EXACT
    two = rows + [{"Item No.": "CHR-ERG-01", "Name": "Other"}]
    assert match_product(two, "CHR-ERG-01").outcome is Outcome.AMBIGUOUS
