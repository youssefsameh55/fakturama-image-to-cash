"""Flat, $ref-free JSON schema for the `record_order` tool (Anthropic) / JSON output (Gemini)."""
from __future__ import annotations

import copy

TOOL_NAME = "record_order"

_STR = {"type": ["string", "null"]}
_MONEY = {"type": ["string", "null"], "description": "Plain decimal string with a dot, e.g. \"250.00\". No currency symbol."}
_PERCENT = {"type": ["string", "null"], "description": "Plain decimal string without %, e.g. \"19\"."}
_DATE = {"type": ["string", "null"], "description": "YYYY-MM-DD"}


def _address() -> dict:
    return {
        "type": ["object", "null"],
        "properties": {
            "name": {**_STR, "description": "First line: company or recipient name"},
            "street": {**_STR, "description": "Street and house number"},
            "zip": {**_STR, "description": "Postal code, as written"},
            "city": _STR,
            "country": {**_STR, "description": "Country name in English, e.g. Germany"},
            "additional": {**_STR, "description": "Any extra address line (c/o, floor, department); null if none"},
        },
        "required": ["name", "street", "zip", "city", "country", "additional"],
    }


ORDER_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "external_reference": {**_STR, "description": "Order / reference number printed on the document"},
        "order_date": _DATE,
        "customer_id": {**_STR, "description": "Customer number if printed, e.g. CUST-1007"},
        "currency": {**_STR, "description": "ISO 4217 code, e.g. EUR"},
        "debtor": {
            "type": "object",
            "properties": {
                "company": _STR,
                "alias": {**_STR, "description": "Alias / short name / customer handle if printed"},
                "first_name": {**_STR, "description": "Contact person's first name"},
                "last_name": {**_STR, "description": "Contact person's last name"},
                "email": _STR,
                "phone": {**_STR, "description": "As printed, including country code"},
                "billing_address": _address(),
                "delivery_address": {**_address(), "description": "Shipping/delivery address; copy billing if the document says same"},
            },
            "required": ["company", "alias", "first_name", "last_name", "email", "phone",
                         "billing_address", "delivery_address"],
        },
        "payment": {
            "type": "object",
            "properties": {
                "method": {**_STR, "description": "Payment method as printed, e.g. Bank Transfer"},
                "status": {"type": ["string", "null"], "description": "One of PAID, UNPAID, OTHER"},
                "payment_date": _DATE,
            },
            "required": ["method", "status", "payment_date"],
        },
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "position": {"type": ["integer", "null"]},
                    "sku": {**_STR, "description": "Article number / SKU exactly as printed"},
                    "description": _STR,
                    "quantity": {"type": ["string", "null"], "description": "Plain decimal string, e.g. \"2\""},
                    "unit": {**_STR, "description": "e.g. pcs; null if not printed"},
                    "unit_net_price": _MONEY,
                    "discount_percent": _PERCENT,
                    "vat_percent": _PERCENT,
                    "line_net_total": _MONEY,
                },
                "required": ["position", "sku", "description", "quantity", "unit", "unit_net_price",
                             "discount_percent", "vat_percent", "line_net_total"],
            },
        },
        "totals": {
            "type": "object",
            "properties": {"net": _MONEY, "vat": _MONEY, "gross": _MONEY},
            "required": ["net", "vat", "gross"],
        },
    },
    "required": ["external_reference", "order_date", "customer_id", "currency", "debtor",
                 "payment", "items", "totals"],
}


def tool_definition() -> dict:
    return {
        "name": TOOL_NAME,
        "description": "Record every field of the order document shown in the image.",
        "input_schema": copy.deepcopy(ORDER_SCHEMA),
    }
