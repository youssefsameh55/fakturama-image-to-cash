from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
KNOWN_METHODS = ("Bank Transfer", "Credit Card", "SEPA Direct Debit")


@pytest.fixture
def expected_dict() -> dict:
    return json.loads((FIXTURES / "order_01.expected.json").read_text(encoding="utf-8"))


@pytest.fixture
def mutate(expected_dict):
    """mutate(lambda d: ...) -> deep copy of the golden dict with a change applied."""
    def _apply(fn):
        d = copy.deepcopy(expected_dict)
        fn(d)
        return d
    return _apply
