"""2 x 2 inch branded QR labels as print-ready PDF, plus an on-screen SVG preview.

Two label styles:
  * "logo"    (default) — company symbol in the centre of the QR code, company
                name and asset code underneath
  * "classic" — company logo across the top, QR below, asset code at the bottom

Two page layouts:
  * "roll" — one 2x2 in page per label (thermal / label printers)
  * "a4"   — 2x2 in labels on A4 sheets (3 x 5 = 15 per sheet) with cut lines

The QR encodes the asset details as plain text, so any phone camera shows them
offline. Passwords are never put on a label.
"""
import base64
import io
import os
from html import escape

import segno
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from .config import BRANDS, LABEL_CONTACT

BRAND_DIR = os.path.join(os.path.dirname(__file__), "static", "brand")
SIZE = 2 * inch            # 144 pt
STYLES = ("logo", "classic")

# Keep the QR coarse enough to scan when printed at 203 dpi (common thermal
# printers): at most version 9 (53 modules) at error level Q, which leaves room
# for a logo in the centre.
MAX_QR_VERSION = 9


def _fmt_date(d):
    return d.strftime("%d-%m-%Y") if d else ""


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
_DROP_ORDER = ["contact", "Purchased", "Dept", "Make/Model", "Location", "User", "Warranty"]


def qr_text(a) -> str:
    drop = []
    text = _qr_lines(a)
    for key in _DROP_ORDER:
        if segno.make(text, error="q", micro=False, boost_error=False).version <= MAX_QR_VERSION:
            break
        drop.append(key)
        text = _qr_lines(a, drop)
    return text


def _make_qr(text: str, with_logo: bool):
    # Q (25 % recovery) leaves plenty of margin for the logo in the centre.
    if with_logo:
        # Prefer H (30 % recovery) when the text is short enough, else Q (25 %).
        qr = segno.make(text, error="h", micro=False, boost_error=False)
        return qr if qr.version <= MAX_QR_VERSION else segno.make(text, error="q", micro=False, boost_error=False)
    qr = segno.make(text, error="m", micro=False, boost_error=False)
    if qr.version > MAX_QR_VERSION:
        qr = segno.make(text, error="l", micro=False, boost_error=False)
    return qr


def _fit(text, font, size, max_w, min_size=4.5):
    while size > min_size and stringWidth(text, font, size) > max_w:
        size -= 0.25
    while stringWidth(text, font, size) > max_w and len(text) > 3:
        text = text[:-2] + "…"
    return text, size


def _brand(a):
    return BRANDS.get(a["company"], {"name": a["company"], "short": a["company"], "color": "#0A2B66",
                                     "logo": "", "mark": "", "label_name": str(a["company"]).upper()})


def _img(name):
    path = os.path.join(BRAND_DIR, name or "")
    return path if name and os.path.isfile(path) else None


def _desc(a):
    mm = " ".join(y for y in [a.get("make"), a.get("model")] if y)
    return " · ".join(x for x in [a.get("category"), mm] if x)


# ---------------------------------------------------------------- layout
# All drawing is described once as a list of operations in top-left
# coordinates (points, 144 = 2 in), then rendered to PDF or SVG.
def _layout(a, style="logo"):
    style = style if style in STYLES else "logo"
    b = _brand(a)
    navy = b["color"]
    S, pad = SIZE, 0.08 * inch
    ops = [("rect", 0, 0, S, S, "#FFFFFF", 0)]
    mark = _img(b.get("mark")) if style == "logo" else None

    if style == "logo":
        qs, qy = 98.0, 8.5
        qr = _make_qr(qr_text(a), with_logo=bool(mark))
        ops.append(("qr", (S - qs) / 2, qy, qs, [list(r) for r in qr.matrix]))
        if mark:
            # Logo window snapped to whole modules, odd count so it stays centred,
            # about 21 % of the width (~4.5 % of the area) — well inside level-Q recovery.
            n = len(qr.matrix)
            mods = max(5, round(n * 0.21))
            mods += (mods % 2 == 0) and (n % 2 == 1)
            m = qs / n
            box = mods * m
            bx, by = (S - box) / 2, qy + (qs - box) / 2
            ops.append(("rect", bx, by, box, box, "#FFFFFF", 0))
            iw, ih = ImageReader(mark).getSize()
            inner = box - 2
            sc = min(inner / iw, inner / ih)
            w, h = iw * sc, ih * sc
            ops.append(("img", mark, (S - w) / 2, by + (box - h) / 2, w, h))
        name, nsz = _fit(b.get("label_name") or b["name"].upper(), "Helvetica-Bold", 7, S - 2 * pad)
        ops.append(("text", S / 2, qy + qs + 9.0, name, "Helvetica-Bold", nsz, navy))
        code, csz = _fit(a["asset_code"], "Helvetica-Bold", 10.5, S - 2 * pad)
        ops.append(("text", S / 2, qy + qs + 22.0, code, "Helvetica-Bold", csz, navy))
        d = _desc(a)
        if d:
            d, dsz = _fit(d, "Helvetica", 5.6, S - 2 * pad)
            ops.append(("text", S / 2, qy + qs + 31.5, d, "Helvetica", dsz, "#333333"))
        return ops

    # classic
    top_h = 0.30 * inch
    logo = _img(b.get("logo"))
    if logo:
        iw, ih = ImageReader(logo).getSize()
        sc = min((S - 2 * pad) / iw, (top_h - 0.09 * inch) / ih)
        w, h = iw * sc, ih * sc
        ops.append(("img", logo, (S - w) / 2, (top_h - h) / 2 - 0.01 * inch, w, h))
    else:
        t, sz = _fit(b["short"], "Helvetica-Bold", 10, S - 2 * pad)
        ops.append(("text", S / 2, top_h / 2 + sz * 0.35, t, "Helvetica-Bold", sz, navy))
    ops.append(("line", pad, top_h, S - pad, top_h, navy, 1.2))
    qs = 1.26 * inch
    qr = _make_qr(qr_text(a), with_logo=False)
    ops.append(("qr", (S - qs) / 2, top_h + 0.075 * inch, qs, [list(r) for r in qr.matrix]))
    code, csz = _fit(a["asset_code"], "Helvetica-Bold", 10, S - 2 * pad)
    ops.append(("text", S / 2, S - 0.175 * inch, code, "Helvetica-Bold", csz, navy))
    d = _desc(a)
    if d:
        d, dsz = _fit(d, "Helvetica", 6, S - 2 * pad)
        ops.append(("text", S / 2, S - 0.07 * inch, d, "Helvetica", dsz, "#333333"))
    return ops


def _runs(matrix):
    """Yield (row, start_col, length) for each horizontal run of dark modules."""
    n = len(matrix)
    for r, row in enumerate(matrix):
        c = 0
        while c < n:
            if row[c]:
                s = c
                while c < n and row[c]:
                    c += 1
                yield r, s, c - s
            else:
                c += 1


# ---------------------------------------------------------------- PDF
def _draw_label(c, a, ox=0.0, oy=0.0, style="logo"):
    """Draw one label with its bottom-left corner at (ox, oy)."""
    S = SIZE
    Y = lambda y: oy + S - y           # top-left → PDF coordinates
    c.saveState()
    for op in _layout(a, style):
        kind = op[0]
        if kind == "rect":
            _, x, y, w, h, fill, rad = op
            c.setFillColor(HexColor(fill))
            if rad:
                c.roundRect(ox + x, Y(y + h), w, h, rad, stroke=0, fill=1)
            else:
                c.rect(ox + x, Y(y + h), w, h, stroke=0, fill=1)
        elif kind == "qr":
            _, x, y, size, matrix = op
            m = size / len(matrix)
            c.setFillColorRGB(0, 0, 0)
            for r, s, ln in _runs(matrix):
                c.rect(ox + x + s * m, Y(y + (r + 1) * m), ln * m + 0.01, m + 0.01, stroke=0, fill=1)
        elif kind == "img":
            _, path, x, y, w, h = op
            c.drawImage(ImageReader(path), ox + x, Y(y + h), w, h, mask="auto")
        elif kind == "line":
            _, x1, y1, x2, y2, col, lw = op
            c.setStrokeColor(HexColor(col))
            c.setLineWidth(lw)
            c.line(ox + x1, Y(y1), ox + x2, Y(y2))
        elif kind == "text":
            _, x, y, t, font, size, col = op
            c.setFillColor(HexColor(col))
            c.setFont(font, size)
            c.drawCentredString(ox + x, Y(y), t)
    c.restoreState()


def labels_pdf(assets, layout: str = "roll", style: str = "logo") -> bytes:
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
            _draw_label(c, a, ox, oy, style)
            c.setStrokeColorRGB(0.75, 0.75, 0.75)       # thin grey cut lines
            c.setLineWidth(0.3)
            c.rect(ox, oy, SIZE, SIZE, stroke=1, fill=0)
    else:
        c = canvas.Canvas(buf, pagesize=(SIZE, SIZE))
        for i, a in enumerate(assets):
            if i:
                c.showPage()
            _draw_label(c, a, style=style)
    c.setTitle("IT asset labels")
    c.save()
    return buf.getvalue()


# ---------------------------------------------------------------- SVG preview
def label_preview_svg(a, style: str = "logo") -> str:
    S = SIZE
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {S:g} {S:g}" role="img" aria-label="Label preview">']
    font = 'font-family="Helvetica, Arial, sans-serif"'
    for op in _layout(a, style):
        kind = op[0]
        if kind == "rect":
            _, x, y, w, h, fill, rad = op
            out.append(f'<rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" rx="{rad}" fill="{fill}"/>')
        elif kind == "qr":
            _, x, y, size, matrix = op
            m = size / len(matrix)
            d = "".join(f"M{x + s * m:.3f} {y + r * m:.3f}h{ln * m:.3f}v{m:.3f}h{-ln * m:.3f}z"
                        for r, s, ln in _runs(matrix))
            out.append(f'<path d="{d}" fill="#000" shape-rendering="crispEdges"/>')
        elif kind == "img":
            _, path, x, y, w, h = op
            data = base64.b64encode(open(path, "rb").read()).decode()
            out.append(f'<image href="data:image/png;base64,{data}" x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}"/>')
        elif kind == "line":
            _, x1, y1, x2, y2, col, lw = op
            out.append(f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" stroke="{col}" stroke-width="{lw}"/>')
        elif kind == "text":
            _, x, y, t, f, size, col = op
            weight = ' font-weight="700"' if "Bold" in f else ""
            out.append(f'<text x="{x:.2f}" y="{y:.2f}" text-anchor="middle" {font}{weight} font-size="{size}" fill="{col}">{escape(t)}</text>')
    out.append("</svg>")
    return "".join(out)
