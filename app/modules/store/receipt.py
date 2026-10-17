"""Server-side PDF receipt (fpdf2, core fonts). Extracted text contains the order id and 'PKR 4500.00'."""
from fpdf import FPDF
from fpdf.enums import XPos, YPos


def money(amount_minor: int, currency: str = "PKR") -> str:
    sign = "-" if amount_minor < 0 else ""
    amount_minor = abs(amount_minor)
    return f"{currency} {sign}{amount_minor // 100}.{amount_minor % 100:02d}"


def _latin1(text) -> str:
    return str(text).encode("latin-1", "replace").decode("latin-1")


def _fit(pdf: FPDF, text: str, width: float) -> str:
    if pdf.get_string_width(text) <= width:
        return text
    while text and pdf.get_string_width(text + "...") > width:
        text = text[:-1]
    return text + "..."


def _line(pdf: FPDF, label: str, value: str, bold: bool = False, shrink: bool = False) -> None:
    """One label/value row. shrink=True keeps the whole value on one line (the order id must stay
    extractable verbatim); otherwise an overlong value is cut with an ellipsis."""
    style = "B" if bold else ""
    pdf.set_font("Helvetica", style, 11)
    pdf.cell(45, 7, _latin1(label), new_x=XPos.RIGHT, new_y=YPos.TOP)
    value, room = _latin1(value), pdf.epw - 46
    size = 11
    while shrink and size > 4 and pdf.get_string_width(value) > room:
        size -= 1
        pdf.set_font("Helvetica", style, size)
    pdf.cell(0, 7, value if shrink else _fit(pdf, value, room), new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def render(payload: dict, names: dict[str, str], signature: str, payment: dict) -> bytes:
    currency = payload["currency"]
    pdf = FPDF(format="A4")
    pdf.set_title(_latin1(f"Receipt {payload['order_id']}"))
    pdf.set_author("NaSCon 26 League Platform")
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 12, "Official Club Store - Payment Receipt", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(2)
    _line(pdf, "Order ID:", payload["order_id"], shrink=True)
    _line(pdf, "Customer:", payload["customer_id"])
    _line(pdf, "Paid at:", payload["paid_at"])
    _line(pdf, "Authorization:", payment["authorization_id"])
    _line(pdf, "Capture:", payment["capture_id"])
    pdf.ln(4)

    pdf.set_font("Helvetica", "B", 11)
    widths = (108, 12, 35, 35)
    for width, head in zip(widths, ("Item", "Qty", "Unit price", "Line total")):
        pdf.cell(width, 8, head, border="B", new_x=XPos.RIGHT, new_y=YPos.TOP)
    pdf.ln(8)
    pdf.set_font("Helvetica", "", 9)
    for item in payload["items"]:
        label = f"{names.get(item['variant_id'], '')} ({item['variant_id']})".strip()
        cells = (label, str(item["quantity"]), money(item["unit_price_minor"], currency),
                 money(item["unit_price_minor"] * item["quantity"], currency))
        for width, text in zip(widths, cells):
            pdf.cell(width, 7, _fit(pdf, _latin1(text), width - 2), new_x=XPos.RIGHT, new_y=YPos.TOP)
        pdf.ln(7)
    pdf.ln(4)
    _line(pdf, "Subtotal:", money(payload["subtotal_minor"], currency))
    _line(pdf, "Discount:", money(payload["discount_minor"], currency))
    _line(pdf, "Total:", money(payload["total_minor"], currency), bold=True)
    pdf.ln(6)
    pdf.set_font("Helvetica", "", 7)
    pdf.multi_cell(0, 4, _latin1(f"HMAC-SHA256 signature: {signature}"))
    return bytes(pdf.output())
