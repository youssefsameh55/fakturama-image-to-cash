"""Order data model. Money, quantities and percentages are Decimal, never float."""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator


def _to_decimal(value: Any) -> Any:
    """Accept '250.00', 250, 250.0, '10%', '€ 40.00'. Floats go via str() to avoid binary noise."""
    if value is None or isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        raise ValueError("boolean is not a number")
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if isinstance(value, str):
        s = value.strip().replace(" ", "").replace(" ", "")
        for token in ("%", "€", "EUR"):
            s = s.replace(token, "")
        if s == "":
            return None
        try:
            return Decimal(s)
        except InvalidOperation as exc:
            raise ValueError(f"not a plain decimal number: {value!r}") from exc
    return value


Dec = Annotated[Decimal, BeforeValidator(_to_decimal)]
OptDec = Annotated[Decimal | None, BeforeValidator(_to_decimal)]


def _blank_to_none(value: Any) -> Any:
    if isinstance(value, str):
        value = " ".join(value.split())
        return value or None
    return value


OptText = Annotated[str | None, BeforeValidator(_blank_to_none)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class PaymentStatus(str, Enum):
    PAID = "PAID"
    UNPAID = "UNPAID"
    OTHER = "OTHER"


class Address(_Model):
    name: OptText = None
    street: OptText = None
    zip: OptText = None
    city: OptText = None
    country: OptText = None
    additional: OptText = None

    @field_validator("zip", mode="before")
    @classmethod
    def _zip_as_string(cls, v: Any) -> Any:
        return str(v) if isinstance(v, int) else v

    def key(self) -> tuple:
        return tuple((getattr(self, f) or "").casefold()
                     for f in ("name", "street", "zip", "city", "country", "additional"))


class Debtor(_Model):
    company: OptText = None
    alias: OptText = None
    first_name: OptText = None
    last_name: OptText = None
    email: OptText = None
    phone: OptText = None
    billing_address: Address | None = None
    delivery_address: Address | None = None

    @property
    def delivery_same_as_billing(self) -> bool:
        if self.delivery_address is None:
            return True
        if self.billing_address is None:
            return False
        return self.delivery_address.key() == self.billing_address.key()


class Payment(_Model):
    method: OptText = None
    status: PaymentStatus = PaymentStatus.OTHER
    payment_date: date | None = None

    @field_validator("status", mode="before")
    @classmethod
    def _status_upper(cls, v: Any) -> Any:
        if v is None:
            return PaymentStatus.OTHER
        if isinstance(v, str):
            v = v.strip().upper()
            return v if v in PaymentStatus.__members__ else PaymentStatus.OTHER
        return v


class Item(_Model):
    position: int | None = None
    sku: OptText = None
    description: OptText = None
    quantity: OptDec = None
    unit: OptText = None
    unit_net_price: OptDec = None
    discount_percent: Dec = Decimal("0")
    vat_percent: OptDec = None
    line_net_total: OptDec = None

    @field_validator("discount_percent", mode="before")
    @classmethod
    def _discount_default(cls, v: Any) -> Any:
        return Decimal("0") if v is None or v == "" else v


class Totals(_Model):
    net: OptDec = None
    vat: OptDec = None
    gross: OptDec = None


class OrderData(_Model):
    external_reference: OptText = None
    order_date: date | None = None
    customer_id: OptText = None
    currency: OptText = None
    debtor: Debtor = Field(default_factory=Debtor)
    payment: Payment = Field(default_factory=Payment)
    items: list[Item] = Field(default_factory=list)
    totals: Totals = Field(default_factory=Totals)

    @field_validator("currency", mode="before")
    @classmethod
    def _currency_upper(cls, v: Any) -> Any:
        if isinstance(v, str):
            v = v.strip().upper()
            return "EUR" if v == "€" else v
        return v

    def to_json_dict(self) -> dict:
        return self.model_dump(mode="json")
