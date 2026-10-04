from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Party(StrictModel):
    cbin_id: str = Field(min_length=1, max_length=40)
    tin: str = Field(min_length=1, max_length=40)


class FiscalMetadata(StrictModel):
    device_serial: str = Field(min_length=1, max_length=100)
    zimra_signature: str = Field(min_length=1, max_length=2048)
    receipt_reference: str | None = Field(default=None, max_length=150)


class Line(StrictModel):
    item_code: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=500)
    quantity: Decimal = Field(gt=0, max_digits=15, decimal_places=6)
    unit_price_minor: int = Field(ge=0, le=10**14, strict=True)
    tax_rate: Decimal = Field(ge=0, le=100, max_digits=7, decimal_places=4)

    @property
    def subtotal(self):
        return int((self.quantity * self.unit_price_minor).quantize(Decimal("1"), ROUND_HALF_UP))

    @property
    def tax(self):
        return int(
            (Decimal(self.subtotal) * self.tax_rate / 100).quantize(Decimal("1"), ROUND_HALF_UP)
        )


class Totals(StrictModel):
    subtotal_minor: int = Field(ge=0, le=10**16, strict=True)
    tax_minor: int = Field(ge=0, le=10**16, strict=True)
    grand_total_minor: int = Field(ge=0, le=10**16, strict=True)


class Invoice(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    document_type: Literal["B2B_INVOICE", "CREDIT_NOTE"] = "B2B_INVOICE"
    external_reference: str = Field(min_length=1, max_length=120)
    issued_at: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    exchange_rate_zig: Decimal | None = Field(default=None, gt=0, max_digits=24, decimal_places=10)
    seller: Party
    buyer: Party
    fiscal_metadata: FiscalMetadata | None = None
    line_items: list[Line] = Field(min_length=1, max_length=1000)
    totals: Totals
    correction_of: str | None = Field(default=None, max_length=40)

    @field_validator("issued_at")
    @classmethod
    def valid_date(cls, value):
        from datetime import date

        date.fromisoformat(value)
        return value

    @model_validator(mode="after")
    def consistency(self):
        if self.seller.cbin_id == self.buyer.cbin_id:
            raise ValueError("Seller and buyer must differ")
        subtotal = sum(line.subtotal for line in self.line_items)
        tax = sum(line.tax for line in self.line_items)
        if (subtotal, tax, subtotal + tax) != (
            self.totals.subtotal_minor,
            self.totals.tax_minor,
            self.totals.grand_total_minor,
        ):
            raise ValueError("Totals do not match line-level half-up rounding")
        if (self.document_type == "CREDIT_NOTE") != bool(self.correction_of):
            raise ValueError(
                "Credit notes must link an original invoice; invoices cannot be corrections"
            )
        return self


class LineAllocation(StrictModel):
    line_index: int = Field(ge=0, le=999)
    account_reference: str = Field(min_length=1, max_length=100)


class Accept(StrictModel):
    sku_mapping: dict[str, str] = Field(min_length=1, max_length=1000)
    supplier_reference: str = Field(min_length=1, max_length=100)
    account_reference: str = Field(min_length=1, max_length=100)
    tax_mapping: dict[str, str] = Field(default_factory=dict, max_length=100)
    bookkeeping: bool = False
    remember_mapping: bool = False
    line_allocations: list[LineAllocation] = Field(default_factory=list, max_length=1000)
    purchase_order_reference: str | None = Field(default=None, max_length=100)


class Reject(StrictModel):
    reason: str = Field(min_length=3, max_length=2000)


class WebhookRegistration(StrictModel):
    url: str = Field(max_length=2048)
    secret_ref: str = Field(pattern=r"^CBIN_WEBHOOK_[A-Z0-9_]+$", max_length=80)


class Replay(StrictModel):
    reason: str = Field(min_length=3, max_length=1000)
