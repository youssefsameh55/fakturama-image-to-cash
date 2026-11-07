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
