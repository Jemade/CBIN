"""Immutable originals and clearly labelled exchange copies; no fiscal issuance here."""

import base64
import hashlib
import io
import textwrap
import unicodedata
from datetime import datetime, timezone
from decimal import Decimal
from html import escape

from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import select

from cbin.db import InvoiceFile
from cbin.service import DomainError, canonical_json, now, record, uid

MAX_FILE_BYTES = 1_000_000


def clean(value):
    # Never let invoice text inject ESC/POS commands or terminal controls.
    return "".join(c for c in str(value or "") if ord(c) >= 32 and ord(c) != 127)


def amount(value, currency):
    # Canonical protocol 1.0 currently accepts two-decimal currencies only.
    return f"{currency} {Decimal(value) / 100:,.2f}"


def receipt_lines(payload):
    p = payload
    lines = [
        "INVOICE EXCHANGE COPY",
        "Fiscal validity is not asserted",
        p["external_reference"],
        p["issued_at"],
    ]
    for label in ("seller", "buyer"):
        party = p[label]
        lines += [
            label.upper(),
            party.get("legal_name") or party["cbin_id"],
            "TIN: " + party["tin"],
        ]
        if party.get("address"):
            lines.append(party["address"])
        if party.get("vat_number"):
            lines.append("VAT: " + party["vat_number"])
    for row in p["line_items"]:
        lines += [
            row["description"],
            f"{row['quantity']} x {amount(row['unit_price_minor'], p['currency'])}",
            "Tax rate: " + str(row["tax_rate"]) + "%",
        ]
    for label, key in [
        ("Net", "subtotal_minor"),
        ("Tax", "tax_minor"),
        ("Total", "grand_total_minor"),
    ]:
        lines.append(label + ": " + amount(p["totals"][key], p["currency"]))
    fiscal = p.get("fiscal_metadata") or {}
    for field in (
        "device_serial",
        "device_id",
        "fiscal_day",
        "receipt_reference",
        "verification_code",
    ):
        if fiscal.get(field):
            lines.append(field.replace("_", " ").title() + ": " + fiscal[field])
    lines += ["Generated from exchanged data.", "Keep the original supplier invoice."]
    return [clean(line) for line in lines]


def escpos(payload, columns=48):
    if columns not in {32, 48}:
        raise DomainError("PRINT_WIDTH_INVALID", "Choose 32 or 48 columns", 422)
    result = bytearray(b"\x1b@\x1ba\x00")
    for line in receipt_lines(payload):
        text = unicodedata.normalize("NFKD", line).encode("ascii", "replace").decode("ascii")
        for part in textwrap.wrap(text, width=columns) or [""]:
            result.extend(part.encode("ascii") + b"\n")
    url = (payload.get("fiscal_metadata") or {}).get("verification_url")
    if url:
        data = url.encode("utf-8")

        def command(body):
            return b"\x1d(k" + len(body).to_bytes(2, "little") + body

        result.extend(command(b"1A\x32\x00"))  # QR model 2, Epson Function 165
        result.extend(command(b"1C\x04"))
        result.extend(command(b"1E\x31"))
        result.extend(command(b"1P0" + data))
        result.extend(command(b"1Q0"))
        result.extend(b"\n")
    result.extend(b"\n\n\n\x1dV\x00")
    return bytes(result)


def invoice_pdf(payload, layout="a4"):
    if layout not in {"a4", "receipt48"}:
        raise DomainError("PRINT_LAYOUT_INVALID", "Choose a4 or receipt48", 422)
    stream = io.BytesIO()
    receipt = layout == "receipt48"
    # Long receipts split into printable pages, rather than silently truncating lines.
    page = (80 * mm, 297 * mm) if receipt else A4
    margin = 4 * mm if receipt else 18 * mm
    doc = SimpleDocTemplate(
        stream,
        pagesize=page,
        leftMargin=margin,
        rightMargin=margin,
        topMargin=margin,
        bottomMargin=margin,
        title="Invoice exchange copy",
    )
    styles = getSampleStyleSheet()
    normal = styles["BodyText"]
    normal.fontSize = 8 if receipt else 10
    story = [
        Paragraph("Invoice exchange copy", styles["Heading2"]),
        Paragraph("Not an original fiscal tax invoice. Fiscal validity is not asserted.", normal),
        Spacer(1, 10),
    ]
    p = payload
    story += [
        Paragraph(escape(p["external_reference"]), styles["Heading3"]),
        Paragraph(escape(p["issued_at"]) + " · " + escape(p["currency"]), normal),
    ]
    for label in ("seller", "buyer"):
        party = p[label]
        details = [
            label.title(),
            party.get("legal_name") or party["cbin_id"],
            "TIN: " + party["tin"],
            party.get("address"),
            ("VAT: " + party["vat_number"]) if party.get("vat_number") else None,
        ]
        story += [Spacer(1, 8), Paragraph("<br/>".join(escape(v) for v in details if v), normal)]
    story += [Spacer(1, 12)]
    if receipt:
        for line in p["line_items"]:
            story += [
                Paragraph(escape(line["description"]), normal),
                Paragraph(
                    escape(
                        f"{line['quantity']} × {amount(line['unit_price_minor'], p['currency'])} · Tax {line['tax_rate']}%"
                    ),
                    normal,
                ),
                Spacer(1, 5),
            ]
    else:
        rows = [["Description / code", "Qty", "Unit price", "Tax %"]]
        for line in p["line_items"]:
            rows.append(
                [
                    Paragraph(
                        escape(line["description"]) + "<br/>" + escape(line["item_code"]), normal
                    ),
                    line["quantity"],
                    amount(line["unit_price_minor"], p["currency"]),
                    line["tax_rate"],
                ]
            )
        table = Table(rows, colWidths=[88 * mm, 15 * mm, 40 * mm, 25 * mm], repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.grey),
                ]
            )
        )
        story.append(table)
    for title, field in [
        ("Net", "subtotal_minor"),
        ("Tax", "tax_minor"),
        ("Total", "grand_total_minor"),
    ]:
        story.append(
            Paragraph(escape(title + ": " + amount(p["totals"][field], p["currency"])), normal)
        )
    fiscal = p.get("fiscal_metadata") or {}
    for field in (
        "device_serial",
        "device_id",
        "fiscal_day",
        "receipt_reference",
        "verification_code",
    ):
        if fiscal.get(field):
            story.append(
                Paragraph(escape(field.replace("_", " ").title() + ": " + fiscal[field]), normal)
            )
    if fiscal.get("verification_url"):
        qr = QrCodeWidget(fiscal["verification_url"])
        bounds = qr.getBounds()
        size = 100
        drawing = Drawing(
            size,
            size,
            transform=[size / (bounds[2] - bounds[0]), 0, 0, size / (bounds[3] - bounds[1]), 0, 0],
        )
        drawing.add(qr)
        story += [Spacer(1, 8), drawing, Paragraph(escape(fiscal["verification_url"]), normal)]
    doc.build(
        story, canvasmaker=lambda *args, **kwargs: Canvas(*args, **{**kwargs, "invariant": 1})
    )
    return stream.getvalue()


def retain_until(timestamp):
    created = datetime.fromtimestamp(timestamp, timezone.utc)
    try:
        return int(created.replace(year=created.year + 6).timestamp())
    except ValueError:  # Feb 29; use March 1 to retain at least six calendar years.
        return int(created.replace(year=created.year + 6, month=3, day=1).timestamp())


def save_file(db, document, kind, filename, media_type, content):
    sha = hashlib.sha256(content).hexdigest()
    existing = db.scalar(
        select(InvoiceFile).where(
            InvoiceFile.document_id == document.id,
            InvoiceFile.kind == kind,
            InvoiceFile.sha256 == sha,
        )
    )
    if existing:
        return existing
    created = now()
    row = InvoiceFile(
        id=uid(),
        document_id=document.id,
        environment=document.environment,
        kind=kind,
        filename=filename,
        media_type=media_type,
        content=content,
        sha256=sha,
        created_at=created,
        retain_until=retain_until(created),
    )
    db.add(row)
    db.flush()
    return row


def archive_exchange(db, document):
    save_file(
        db,
        document,
        "canonical",
        "invoice.json",
        "application/json",
        canonical_json(document.payload).encode(),
    )
    save_file(
        db,
        document,
        "exchange_copy",
        "invoice-exchange-copy.pdf",
        "application/pdf",
        invoice_pdf(document.payload),
    )


def decode_upload(body):
    try:
        data = base64.b64decode(body.content_base64, validate=True)
    except ValueError:
        raise DomainError("FILE_ENCODING_INVALID", "Use valid base64 file content", 422) from None
    if not data or len(data) > MAX_FILE_BYTES:
        raise DomainError("FILE_TOO_LARGE", "Maximum attachment size is 1 MB", 413)
    signatures = {
        "application/pdf": b"%PDF-",
        "image/png": b"\x89PNG\r\n\x1a\n",
        "image/jpeg": b"\xff\xd8\xff",
    }
    if not data.startswith(signatures[body.media_type]):
        raise DomainError("FILE_TYPE_INVALID", "File content does not match its declared type", 422)
    return data


def upload_original(db, credential, document, body):
    seller = document.seller_id == credential.business_id and credential.role in {
        "submitter",
        "admin",
    }
    buyer = document.buyer_id == credential.business_id and credential.role in {"reviewer", "admin"}
    if not seller and not buyer:
        raise DomainError(
            "FORBIDDEN", "Only the seller or buyer reviewer may add invoice evidence", 403
        )
    if document.status not in {"queued", "delivered", "under_review"}:
        raise DomainError("EVIDENCE_SEALED", "Evidence is sealed after the buyer decision", 409)
    data = decode_upload(body)
    originals = db.scalars(
        select(InvoiceFile).where(
            InvoiceFile.document_id == document.id,
            InvoiceFile.kind.in_(["seller_original", "buyer_scan"]),
        )
    ).all()
    sha = hashlib.sha256(data).hexdigest()
    for row in originals:
        if row.sha256 == sha:
            return row
    if len(originals) >= 8 or sum(len(row.content) for row in originals) + len(data) > 4_000_000:
        raise DomainError("FILE_QUOTA_EXCEEDED", "Invoice evidence limit reached", 413)
    row = save_file(
        db,
        document,
        "seller_original" if seller else "buyer_scan",
        body.filename,
        body.media_type,
        data,
    )
    record(
        db,
        credential,
        "invoice.evidence_stored",
        document,
        {"file_id": row.id, "sha256": sha, "kind": row.kind},
    )
    return row


def file_summary(row):
    return {
        "id": row.id,
        "kind": row.kind,
        "filename": row.filename,
        "media_type": row.media_type,
        "sha256": row.sha256,
        "size_bytes": len(row.content),
        "created_at": row.created_at,
        "retain_until": row.retain_until,
    }


def files_for(db, document):
    return db.scalars(
        select(InvoiceFile)
        .where(
            InvoiceFile.document_id == document.id, InvoiceFile.environment == document.environment
        )
        .order_by(InvoiceFile.created_at, InvoiceFile.id)
    ).all()


def evidence_packet(db, document):
    """One PDF for providers with one attachment slot; originals embedded byte-for-byte."""
    from pypdf import PdfReader, PdfWriter

    existing = [f for f in files_for(db, document) if f.kind == "erp_packet"]
    if existing:
        return existing[0]
    files = files_for(db, document)
    copy = next((f for f in files if f.kind == "exchange_copy"), None)
    if copy is None:
        archive_exchange(db, document)
        files = files_for(db, document)
        copy = next(f for f in files if f.kind == "exchange_copy")
    writer = PdfWriter()
    writer.append(PdfReader(io.BytesIO(copy.content)))
    manifest = []
    for file in files:
        # A unique prefix prevents duplicate source filenames replacing attachments.
        writer.add_attachment(file.id + "-" + file.filename, file.content)
        manifest.append(
            {
                "filename": file.filename,
                "kind": file.kind,
                "sha256": file.sha256,
                "embedded_name": file.id + "-" + file.filename,
            }
        )
    writer.add_attachment("cbin-manifest.json", canonical_json(manifest).encode())
    buffer = io.BytesIO()
    writer.write(buffer)
    return save_file(
        db,
        document,
        "erp_packet",
        "invoice-evidence-packet.pdf",
        "application/pdf",
        buffer.getvalue(),
    )
