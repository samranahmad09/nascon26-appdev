"""Server-side PDF receipt (fpdf2, core fonts). Extracted text contains the order id and 'PKR 4500.00'."""
from fpdf import FPDF
from fpdf.enums import XPos, YPos


def money(amount_minor: int, currency: str = "PKR") -> str:
    sign = "-" if amount_minor < 0 else ""
    amount_minor = abs(amount_minor)
    return f"{currency} {sign}{amount_minor // 100}.{amount_minor % 100:02d}"


def _latin1(text) -> str:
    return str(text).encode("latin-1", "replace").decode("latin-1")


def _line(pdf: FPDF, label: str, value: str, bold: bool = False) -> None:
    pdf.set_font("Helvetica", "B" if bold else "", 11)
    pdf.cell(45, 7, _latin1(label), new_x=XPos.RIGHT, new_y=YPos.TOP)
    pdf.cell(0, 7, _latin1(value), new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def render(payload: dict, names: dict[str, str], signature: str, payment: dict) -> bytes:
    currency = payload["currency"]
    pdf = FPDF(format="A4")
    pdf.set_title(_latin1(f"Receipt {payload['order_id']}"))
    pdf.set_author("NaSCon 26 League Platform")
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 12, "Official Club Store - Payment Receipt", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(2)
    _line(pdf, "Order ID:", payload["order_id"])
    _line(pdf, "Customer:", payload["customer_id"])
    _line(pdf, "Paid at:", payload["paid_at"])
    _line(pdf, "Authorization:", payment["authorization_id"])
    _line(pdf, "Capture:", payment["capture_id"])
    pdf.ln(4)

    pdf.set_font("Helvetica", "B", 11)
    widths = (80, 20, 40, 40)
    for width, head in zip(widths, ("Item", "Qty", "Unit price", "Line total")):
        pdf.cell(width, 8, head, border="B", new_x=XPos.RIGHT, new_y=YPos.TOP)
    pdf.ln(8)
    pdf.set_font("Helvetica", "", 10)
    for item in payload["items"]:
        label = f"{names.get(item['variant_id'], '')} ({item['variant_id']})".strip()
        cells = (label, str(item["quantity"]), money(item["unit_price_minor"], currency),
                 money(item["unit_price_minor"] * item["quantity"], currency))
        for width, text in zip(widths, cells):
            pdf.cell(width, 7, _latin1(text)[:60], new_x=XPos.RIGHT, new_y=YPos.TOP)
        pdf.ln(7)
    pdf.ln(4)
    _line(pdf, "Subtotal:", money(payload["subtotal_minor"], currency))
    _line(pdf, "Discount:", money(payload["discount_minor"], currency))
    _line(pdf, "Total:", money(payload["total_minor"], currency), bold=True)
    pdf.ln(6)
    pdf.set_font("Helvetica", "", 7)
    pdf.multi_cell(0, 4, _latin1(f"HMAC-SHA256 signature: {signature}"))
    return bytes(pdf.output())
