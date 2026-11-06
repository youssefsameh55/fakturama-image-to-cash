"""Extraction prompt. Bump PROMPT_VERSION whenever the text or schema changes (invalidates the cache)."""
from __future__ import annotations

import json
from collections.abc import Sequence

from i2c.extract.schema import ORDER_SCHEMA, TOOL_NAME

PROMPT_VERSION = "v1"

SYSTEM = """You transcribe order documents into structured data for an invoicing system.

Rules:
- Transcribe only what is visible in the image. Never invent, infer or complete values. If a field is not printed, use null.
- Money: plain decimal strings with a dot and two decimals, no currency symbol or thousands separator (e.g. "1250.00").
- Percentages: plain decimal strings without the % sign (e.g. "19", "10").
- Quantities: plain decimal strings (e.g. "2").
- Dates: YYYY-MM-DD.
- Contact person: split the name into first_name (first word) and last_name (the rest). Do not put titles (Mr, Ms, Dr) in either.
- Addresses: split into name (first line: company or recipient), street (street + number), zip, city, country (English name). Put any other address line in "additional".
- If the document says the delivery address is the same as the billing address, copy the billing address into delivery_address.
- Payment status: PAID, UNPAID, or OTHER, uppercase. payment_date only if a payment date is printed.
- Items: one entry per line item, in the printed order, with position starting at 1. sku is the article number exactly as printed.
- discount_percent: the line's discount; "0" if the line shows no discount.
- line_net_total: the line total as printed (net, after discount).
- totals: net, VAT and gross totals as printed."""


def user_instruction(use_tool: bool) -> str:
    if use_tool:
        return f"Extract the order shown in this image and record it with the {TOOL_NAME} tool."
    return (
        "Extract the order shown in this image. Reply with a single JSON object (no prose, no code fences) "
        "that conforms to this JSON schema:\n" + json.dumps(ORDER_SCHEMA, indent=1)
    )


def retry_feedback(issues: Sequence[object], previous: dict | None) -> str:
    """Text appended on the retry: the validation problems plus the previous answer."""
    lines = ["Your previous extraction failed these checks:"]
    for issue in issues:
        lines.append(f"- {issue}")
    if previous is not None:
        lines.append("")
        lines.append("Previous extraction:")
        lines.append(json.dumps(previous, indent=1, ensure_ascii=False))
    lines.append("")
    lines.append("Look at the image again and correct the values that you misread. "
                 "Transcribe what is printed; do not adjust numbers just to make the checks pass.")
    return "\n".join(lines)
