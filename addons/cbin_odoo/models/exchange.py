"""Odoo 18 prototype. Install and validate in a sandbox before enabling any cron."""

import json
import os
from decimal import ROUND_HALF_UP, Decimal

import requests
from odoo import api, fields, models


def minor(value):
    return int((Decimal(str(value)) * 100).quantize(Decimal("1"), ROUND_HALF_UP))


class Partner(models.Model):
    _inherit = "res.partner"
    cbin_business_id = fields.Char(groups="account.group_account_manager")
    cbin_tin = fields.Char(groups="account.group_account_manager")


class Company(models.Model):
    _inherit = "res.company"
    cbin_business_id = fields.Char(groups="account.group_account_manager")
    cbin_tin = fields.Char(groups="account.group_account_manager")


class Outbound(models.Model):
    _name = "cbin.outbound"
    _description = "CBIN Durable Outbound Invoice"
    _order = "id"

    move_id = fields.Many2one("account.move", required=True, ondelete="restrict")
    payload = fields.Text(readonly=True)
    state = fields.Selection(
        [("pending", "Pending"), ("sent", "Sent"), ("blocked", "Blocked")],
        default="pending",
        required=True,
    )
    error_code = fields.Char(readonly=True)
    _sql_constraints = [("move_unique", "unique(move_id)", "Invoice is already queued")]

    @api.model
    def _cron_send(self):
        # One cron process owns the local queue. Parallel processing needs SKIP LOCKED.
        url, key = os.getenv("CBIN_URL"), os.getenv("CBIN_API_KEY")
        if not url or not url.startswith("https://") or not key:
            return
        for row in self.search([("state", "=", "pending")], limit=50):
            try:
                response = requests.post(
                    url.rstrip("/") + "/v1/documents",
                    data=row.payload,
                    headers={
                        "Authorization": f"Bearer {key}",
                        "Content-Type": "application/json",
                        "Idempotency-Key": f"odoo:{self.env.cr.dbname}:{row.move_id.id}",
                    },
                    timeout=20,
                    allow_redirects=False,
                )
                if response.status_code == 202:
                    row.write({"state": "sent", "error_code": False})
                elif response.status_code in {400, 403, 404, 422}:
                    row.write({"state": "blocked", "error_code": f"HTTP_{response.status_code}"})
            except requests.RequestException:
                # Same stable key is retained after lost acknowledgement or connectivity loss.
                continue


class AccountMove(models.Model):
    _inherit = "account.move"

    def action_post(self):
        result = super().action_post()
        for move in self:
            if (
                move.move_type != "out_invoice"
                or not move.company_id.cbin_business_id
                or not move.partner_id.cbin_business_id
            ):
                continue
            queue = self.env["cbin.outbound"].sudo()
            if queue.search_count([("move_id", "=", move.id)]):
                continue
            try:
                payload = move._cbin_payload()
                queue.create(
                    {"move_id": move.id, "payload": json.dumps(payload), "state": "pending"}
                )
            except ValueError:
                # Unsupported invoice shape never interrupts the seller's existing posting path.
                queue.create(
                    {
                        "move_id": move.id,
                        "state": "blocked",
                        "error_code": "NORMALIZATION_UNSUPPORTED",
                    }
                )
        return result

    def _cbin_payload(self):
        self.ensure_one()
        if Decimal(str(self.currency_id.rounding)) != Decimal("0.01"):
            raise ValueError("Prototype requires a two-decimal currency")
        lines = []
        for line in self.invoice_line_ids.filtered(lambda line: line.display_type == "product"):
            if line.discount or len(line.tax_ids) > 1 or line.quantity <= 0:
                raise ValueError(
                    "Discount, multiple tax or nonpositive quantity requires an extension"
                )
            tax = line.tax_ids
            if tax and (tax.amount_type != "percent" or tax.price_include):
                raise ValueError("Prototype requires tax-exclusive percentage tax")
            if not line.product_id.default_code:
                raise ValueError("Product code required")
            lines.append(
                {
                    "item_code": line.product_id.default_code,
                    "description": line.name,
                    "quantity": str(line.quantity),
                    "unit_price_minor": minor(line.price_unit),
                    "tax_rate": str(tax.amount if tax else 0),
                }
            )
        subtotal = sum(
            int(
                (Decimal(line["quantity"]) * line["unit_price_minor"]).quantize(
                    Decimal("1"), ROUND_HALF_UP
                )
            )
            for line in lines
        )
        tax_total = sum(
            int(
                (
                    (Decimal(line["quantity"]) * line["unit_price_minor"]).quantize(
                        Decimal("1"), ROUND_HALF_UP
                    )
                    * Decimal(line["tax_rate"])
                    / 100
                ).quantize(Decimal("1"), ROUND_HALF_UP)
            )
            for line in lines
        )
        tax_total = int(tax_total)
        if (
            not lines
            or subtotal != minor(self.amount_untaxed)
            or subtotal + tax_total != minor(self.amount_total)
        ):
            raise ValueError("Rounding or amount mismatch")
        return {
            "schema_version": "1.0",
            "document_type": "B2B_INVOICE",
            "external_reference": self.name,
            "issued_at": str(self.invoice_date),
            "currency": self.currency_id.name,
            "seller": {
                "cbin_id": self.company_id.cbin_business_id,
                "tin": self.company_id.cbin_tin,
            },
            "buyer": {"cbin_id": self.partner_id.cbin_business_id, "tin": self.partner_id.cbin_tin},
            "line_items": lines,
            "totals": {
                "subtotal_minor": subtotal,
                "tax_minor": tax_total,
                "grand_total_minor": subtotal + tax_total,
            },
        }
