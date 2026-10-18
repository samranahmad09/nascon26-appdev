"""QR artwork for GET /audit/v1/qr/{entity_id}/export.{svg,png,pdf} (spec 5.6).

Every format encodes the entity's short_url, which never changes, so the files are cached per short_url.
The PDF is an A4 poster written without stream compression so the entity id and short url stay readable
as plain text in the file.
"""
import io

import segno
from fpdf import FPDF

MEDIA_TYPES = {"svg": "image/svg+xml", "png": "image/png", "pdf": "application/pdf"}

KIND_LABELS = {"match": "Match", "player": "Player", "club": "Club", "costream": "Co-stream"}


def _code(short_url: str):
    return segno.make(short_url, error="m", micro=False)


def svg(short_url: str, entity_id: str) -> bytes:
    out = io.BytesIO()
    _code(short_url).save(out, kind="svg", scale=8, border=4, xmldecl=False, title=short_url,
                          desc=f"NaSCon 26 dynamic QR for {entity_id}", dark="#000000", light="#ffffff")
    return out.getvalue()


def png(short_url: str, scale: int = 10) -> bytes:
    out = io.BytesIO()
    _code(short_url).save(out, kind="png", scale=scale, border=4, dark="#000000", light="#ffffff")
    return out.getvalue()


def _draw_code(doc: FPDF, short_url: str, x: float, y: float, size: float) -> None:
    """Vector modules (one rectangle per horizontal run) so the poster prints sharp at any size."""
    rows = [list(row) for row in _code(short_url).matrix_iter(scale=1, border=4)]
    cell = size / len(rows)
    doc.set_fill_color(255, 255, 255)
    doc.rect(x, y, size, size, style="F")
    doc.set_fill_color(0, 0, 0)
    for r, row in enumerate(rows):
        start = None
        for c, dark in enumerate(row + [False]):
            if dark and start is None:
                start = c
            elif not dark and start is not None:
                doc.rect(x + start * cell, y + r * cell, (c - start) * cell, cell, style="F")
                start = None


def pdf(short_url: str, entity_id: str, entity_type: str) -> bytes:
    label = KIND_LABELS.get(entity_type, "Entity")
    doc = FPDF(orientation="P", unit="mm", format="A4")
    doc.set_compression(False)
    doc.set_title(f"NaSCon 26 QR poster {entity_id}")
    doc.set_subject(short_url)
    doc.set_author("NaSCon 26 League Platform")
    doc.set_auto_page_break(False)
    doc.add_page()
    doc.set_font("Helvetica", "B", 30)
    doc.set_y(22)
    doc.cell(0, 14, "NaSCon '26", align="C", new_x="LMARGIN", new_y="NEXT")
    doc.set_font("Helvetica", "", 16)
    doc.cell(0, 10, "Scan to open in your browser. No app or account needed.", align="C",
             new_x="LMARGIN", new_y="NEXT")
    _draw_code(doc, short_url, x=40, y=58, size=130)
    doc.set_y(194)
    doc.set_font("Helvetica", "B", 18)
    doc.cell(0, 11, f"{label}: {entity_id}", align="C", new_x="LMARGIN", new_y="NEXT")
    doc.set_font("Courier", "", 14)
    doc.cell(0, 9, short_url, align="C", new_x="LMARGIN", new_y="NEXT")
    doc.set_font("Helvetica", "", 11)
    doc.set_x(25)
    doc.multi_cell(160, 6, "This code always opens the current page: live, replay or notice. "
                           "The poster never needs reprinting.", align="C", new_x="LMARGIN", new_y="NEXT")
    doc.cell(0, 8, f"App link: nascon://entity/{entity_id}", align="C")
    return bytes(doc.output())


def render(fmt: str, short_url: str, entity_id: str, entity_type: str) -> bytes:
    if fmt == "svg":
        return svg(short_url, entity_id)
    if fmt == "png":
        return png(short_url)
    return pdf(short_url, entity_id, entity_type)
