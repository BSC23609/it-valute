"""2 x 2 inch branded QR labels as print-ready PDF.

Two layouts:
  * "roll"  — one 2x2 in page per label (thermal / label printers)
  * "a4"    — 2x2 in labels on A4 sheets (3 x 5 = 15 per sheet) with cut marks
The QR encodes the asset details as plain text, so any phone camera shows them
offline. Passwords are never put on a label.
"""
import io
import os

import segno
from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from .config import BRANDS, LABEL_CONTACT

BRAND_DIR = os.path.join(os.path.dirname(__file__), "static", "brand")
SIZE = 2 * inch


def _fmt_date(d):
    return d.strftime("%d-%m-%Y") if d else ""


# The QR must stay small enough to scan when printed at 203 dpi (common thermal
# label printers): at most version 9 (53 modules) => ~5 printer dots per module.
MAX_QR_VERSION = 9


def _clip(v, n=32):
    v = (v or "").strip().replace("\n", " ")
    return v if len(v) <= n else v[: n - 1] + "…"


def _qr_lines(a, drop=()):
    brand = BRANDS.get(a["company"], {"name": a["company"]})
    make_model = " ".join(x for x in [a.get("make"), a.get("model")] if x)
    rows = [
        ("Asset", a["asset_code"]),
        ("Type", a.get("category")),
        ("Make/Model", _clip(make_model)),
        ("S/N", _clip(a.get("serial_no"))),
        ("User", _clip(a.get("assigned_to"), 28)),
        ("Dept", _clip(a.get("department"), 24)),
        ("Location", _clip(a.get("location"), 28)),
        ("Purchased", _fmt_date(a.get("purchase_date"))),
        ("Warranty", _fmt_date(a.get("warranty_end"))),
    ]
    lines = [brand["name"]] + [f"{k}: {v}" for k, v in rows if v and k not in drop]
    if LABEL_CONTACT and "contact" not in drop:
        lines.append(LABEL_CONTACT)
    return "\n".join(lines)


# Least important lines are dropped first if the text is too long for a scannable code.
_DROP_ORDER = ["contact", "Purchased", "Dept", "Make/Model", "Location"]


def qr_text(a) -> str:
    drop = []
    text = _qr_lines(a)
    for key in _DROP_ORDER:
        if segno.make(text, error="l", micro=False, boost_error=False).version <= MAX_QR_VERSION:
            break
        drop.append(key)
        text = _qr_lines(a, drop)
    return text


def _make_qr(text: str):
    qr = segno.make(text, error="m", micro=False, boost_error=False)
    if qr.version > MAX_QR_VERSION:
        qr = segno.make(text, error="l", micro=False, boost_error=False)
    return qr


def _fit_text(c, text, font, size, max_w, min_size=4.5):
    while size > min_size and c.stringWidth(text, font, size) > max_w:
        size -= 0.25
    while c.stringWidth(text, font, size) > max_w and len(text) > 3:
        text = text[:-2] + "…"
    return text, size


def _draw_label(c, a, ox=0.0, oy=0.0):
    """Draw one 2x2 in label with its bottom-left corner at (ox, oy)."""
    brand = BRANDS.get(a["company"], {"name": a["company"], "short": a["company"], "color": "#0A2B66", "logo": ""})
    navy = HexColor(brand["color"])
    pad = 0.08 * inch
    top_h = 0.30 * inch

    c.saveState()
    c.setFillColor(white)
    c.rect(ox, oy, SIZE, SIZE, stroke=0, fill=1)

    # --- brand band: logo (or company short name) + navy rule ---
    logo_path = os.path.join(BRAND_DIR, brand.get("logo") or "")
    band_y = oy + SIZE - top_h
    if brand.get("logo") and os.path.isfile(logo_path):
        img = ImageReader(logo_path)
        iw, ih = img.getSize()
        max_w, max_h = SIZE - 2 * pad, top_h - 0.09 * inch
        scale = min(max_w / iw, max_h / ih)
        w, h = iw * scale, ih * scale
        c.drawImage(img, ox + (SIZE - w) / 2, band_y + (top_h - h) / 2 + 0.01 * inch, w, h, mask="auto")
    else:
        text, size = _fit_text(c, brand["short"], "Helvetica-Bold", 10, SIZE - 2 * pad)
        c.setFont("Helvetica-Bold", size)
        c.setFillColor(navy)
        c.drawCentredString(ox + SIZE / 2, band_y + top_h / 2 - size * 0.35, text)
    c.setStrokeColor(navy)
    c.setLineWidth(1.2)
    c.line(ox + pad, band_y, ox + SIZE - pad, band_y)

    # --- QR code (vector, one rect per run of dark modules) ---
    qr = _make_qr(qr_text(a))
    matrix = [list(r) for r in qr.matrix]
    n = len(matrix)
    qr_size = 1.26 * inch
    m = qr_size / n
    qx = ox + (SIZE - qr_size) / 2
    qy_top = band_y - 0.075 * inch
    c.setFillColorRGB(0, 0, 0)
    for r, row in enumerate(matrix):
        y = qy_top - (r + 1) * m
        col = 0
        while col < n:
            if row[col]:
                start = col
                while col < n and row[col]:
                    col += 1
                c.rect(qx + start * m, y, (col - start) * m + 0.01, m + 0.01, stroke=0, fill=1)
            else:
                col += 1

    # --- asset code + short description ---
    c.setFillColor(navy)
    code, size = _fit_text(c, a["asset_code"], "Helvetica-Bold", 10, SIZE - 2 * pad)
    c.setFont("Helvetica-Bold", size)
    c.drawCentredString(ox + SIZE / 2, oy + 0.175 * inch, code)
    desc = " · ".join(x for x in [a.get("category"), " ".join(y for y in [a.get("make"), a.get("model")] if y)] if x)
    if desc:
        c.setFillColorRGB(0.2, 0.2, 0.2)
        desc, dsize = _fit_text(c, desc, "Helvetica", 6, SIZE - 2 * pad)
        c.setFont("Helvetica", dsize)
        c.drawCentredString(ox + SIZE / 2, oy + 0.07 * inch, desc)
    c.restoreState()


def label_preview_svg(a) -> str:
    """On-screen preview drawn with the same geometry as the PDF (units = points, 144 = 2 in)."""
    from html import escape
    import base64
    from reportlab.pdfbase.pdfmetrics import stringWidth

    brand = BRANDS.get(a["company"], {"name": a["company"], "short": a["company"], "color": "#0A2B66", "logo": ""})
    navy = brand["color"]
    S, pad, top_h = 144.0, 0.08 * 72, 0.30 * 72
    band = top_h  # y of rule line from top
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {S:g} {S:g}" role="img" aria-label="Label preview">',
           f'<rect width="{S:g}" height="{S:g}" fill="#fff"/>']

    def fit(text, font, size, max_w, min_size=4.5):
        while size > min_size and stringWidth(text, font, size) > max_w:
            size -= 0.25
        while stringWidth(text, font, size) > max_w and len(text) > 3:
            text = text[:-2] + "…"
        return text, size

    logo_path = os.path.join(BRAND_DIR, brand.get("logo") or "")
    if brand.get("logo") and os.path.isfile(logo_path):
        iw, ih = ImageReader(logo_path).getSize()
        scale = min((S - 2 * pad) / iw, (top_h - 0.09 * 72) / ih)
        w, h = iw * scale, ih * scale
        data = base64.b64encode(open(logo_path, "rb").read()).decode()
        out.append(f'<image href="data:image/png;base64,{data}" x="{(S - w) / 2:.2f}" '
                   f'y="{(top_h - h) / 2 - 0.01 * 72:.2f}" width="{w:.2f}" height="{h:.2f}"/>')
    else:
        t, size = fit(brand["short"], "Helvetica-Bold", 10, S - 2 * pad)
        out.append(f'<text x="{S / 2}" y="{top_h / 2 + size * 0.35:.2f}" text-anchor="middle" '
                   f'font-family="Helvetica, Arial, sans-serif" font-weight="700" font-size="{size}" fill="{navy}">{escape(t)}</text>')
    out.append(f'<line x1="{pad}" y1="{band}" x2="{S - pad}" y2="{band}" stroke="{navy}" stroke-width="1.2"/>')

    qr = _make_qr(qr_text(a))
    matrix = [list(r) for r in qr.matrix]
    n = len(matrix)
    qs = 1.26 * 72
    m = qs / n
    qx, qy = (S - qs) / 2, band + 0.075 * 72
    path = []
    for r, row in enumerate(matrix):
        col = 0
        while col < n:
            if row[col]:
                start = col
                while col < n and row[col]:
                    col += 1
                path.append(f"M{qx + start * m:.3f} {qy + r * m:.3f}h{(col - start) * m:.3f}v{m:.3f}h{-(col - start) * m:.3f}z")
            else:
                col += 1
    out.append(f'<path d="{"".join(path)}" fill="#000" shape-rendering="crispEdges"/>')

    code, size = fit(a["asset_code"], "Helvetica-Bold", 10, S - 2 * pad)
    out.append(f'<text x="{S / 2}" y="{S - 0.175 * 72:.2f}" text-anchor="middle" font-family="Helvetica, Arial, sans-serif" '
               f'font-weight="700" font-size="{size}" fill="{navy}">{escape(code)}</text>')
    desc = " · ".join(x for x in [a.get("category"), " ".join(y for y in [a.get("make"), a.get("model")] if y)] if x)
    if desc:
        desc, dsize = fit(desc, "Helvetica", 6, S - 2 * pad)
        out.append(f'<text x="{S / 2}" y="{S - 0.07 * 72:.2f}" text-anchor="middle" font-family="Helvetica, Arial, sans-serif" '
                   f'font-size="{dsize}" fill="#333">{escape(desc)}</text>')
    out.append("</svg>")
    return "".join(out)


def labels_pdf(assets, layout: str = "roll") -> bytes:
    buf = io.BytesIO()
    if layout == "a4":
        pw, ph = A4
        cols, rows = 3, 5
        mx = (pw - cols * SIZE) / 2
        my = (ph - rows * SIZE) / 2
        c = canvas.Canvas(buf, pagesize=A4)
        per = cols * rows
        for i, a in enumerate(assets):
            k = i % per
            if k == 0 and i:
                c.showPage()
            col, row = k % cols, k // cols
            ox, oy = mx + col * SIZE, ph - my - (row + 1) * SIZE
            _draw_label(c, a, ox, oy)
            # thin grey cut lines
            c.setStrokeColorRGB(0.75, 0.75, 0.75)
            c.setLineWidth(0.3)
            c.rect(ox, oy, SIZE, SIZE, stroke=1, fill=0)
    else:
        c = canvas.Canvas(buf, pagesize=(SIZE, SIZE))
        for i, a in enumerate(assets):
            if i:
                c.showPage()
            _draw_label(c, a)
    c.setTitle("IT asset labels")
    c.save()
    return buf.getvalue()
