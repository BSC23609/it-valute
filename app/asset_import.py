"""Read an asset register workbook (one sheet per company / asset type) into asset rows."""
import datetime as dt
import re

from .config import BRANDS, CATEGORIES

EMPTY = {"", "n/a", "na", "n.a", "nil", "none", "-", "--", "not visible", "not available", "nill", "null"}

# header text -> asset field
HEADERS = {
    "category": ["asset type", "device type", "type", "nv", "category", "asset", "equipment", "item"],
    "make": ["brand", "printer brand", "make", "manufacturer", "company make"],
    "model": ["model", "printer model", "model no", "model number"],
    "serial_no": ["serial number", "serial no", "serial", "s/n", "sn", "service tag"],
    "cpu": ["processor", "cpu"],
    "ram": ["ram", "memory"],
    "disk": ["hard disk /ssd", "hard disk / ssd", "hard disk", "hdd", "ssd", "storage", "hdd/ssd", "disk"],
    "os": ["operating system", "os"],
    "hostname": ["computer name", "hostname", "host name", "pc name", "system name", "device name"],
    "assigned_to": ["user name", "username", "user", "assigned to", "employee", "employee name", "used by"],
    "department": ["department", "dept"],
    "location": ["location", "site", "branch", "office"],
    "status": ["asset status", "printer status", "device status", "status", "condition"],
    "notes": ["remarks", "remark", "notes", "note", "comments"],
    "ip_address": ["ip address", "ip"],
    "mac_address": ["mac address", "mac"],
    "vendor": ["vendor", "supplier"],
    "invoice_no": ["invoice no", "invoice number", "invoice"],
    "purchase_date": ["purchase date", "date of purchase", "purchased on"],
    "warranty_end": ["warranty end", "warranty", "warranty till", "warranty expiry"],
    "cost": ["cost", "price", "amount", "value"],
    "name": ["description", "asset name", "name"],
}
SERIAL_COLS = {"sno", "slno", "srno", "serialno.", "#", "no", "sl"}


def norm(h):
    return " ".join(str(h or "").replace("\n", " ").replace("_", " ").strip().lower().rstrip(".").split())


def clean(v):
    if v is None:
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if isinstance(v, (dt.datetime, dt.date)):
        return v.date() if isinstance(v, dt.datetime) else v
    s = " ".join(str(v).replace("`", "").split())
    return None if s.lower() in EMPTY else s


def company_for_sheet(title):
    t = title.lower()
    if "metfraa" in t:
        return "METFRAA"
    if "crayon" in t:
        return "CRAYON"
    if "g2" in t or "salem" in t:
        return "G2"
    if "bharat" in t or "bsc" in t:
        return "BSC"
    if "group" in t or "bsg" in t:
        return "GROUP"
    for key, b in BRANDS.items():
        if b["short"].lower() in t:
            return key
    return None


def category_for(type_text, sheet_title):
    t = f"{type_text or ''}".lower()
    rules = [
        (("laptop", "notebook"), "Laptop"),
        (("desktop", "n-computing", "n- computing", "ncomputing", "computer", "pc", "workstation", "thin client"), "Desktop"),
        (("printer", "scanner", "mfp"), "Printer / Scanner"),
        (("nvr", "dvr", "cctv", "camera"), "CCTV / NVR"),
        (("access point", "wireless", "wi-fi", "wifi", "eap"), "Wi-Fi access point"),
        (("switch", "swich"), "Network switch"),
        (("firewall", "router", "gateway", "vpn", "fortigate"), "Router / Firewall"),
        (("server",), "Server"),
        (("monitor",), "Monitor"),
        (("ups",), "UPS"),
        (("biometric", "attendance"), "Biometric device"),
        (("nas", "storage"), "Storage / NAS"),
        (("projector", "display", "tv"), "Projector / Display"),
        (("mobile", "tablet", "phone"), "Mobile / Tablet"),
        (("epabx", "intercom"), "Phone / EPABX"),
    ]
    for words, cat in rules:
        if any(re.search(rf"\b{re.escape(w)}", t) for w in words):
            return cat
    s = sheet_title.lower()
    if not t:
        if "printer" in s:
            return "Printer / Scanner"
    return "Other"


def status_for(status, remark=None):
    """Map the sheet's status (+ remark) to one of STATUSES."""
    st = re.sub(r"\s+", "", (status or "").lower())        # "In Active / Spare" -> "inactive/spare"
    rm = (remark or "").lower()
    broken = any(w in rm for w in ("damage", "faulty", "not working", "dead", "repair"))
    spare = any(w in st + " " + rm for w in ("spare", "sapre", "stock", "idle"))
    if any(w in st for w in ("scrap", "dispos", "lost")):
        return "Lost / Disposed"
    if "retire" in st:
        return "Retired"
    if "repair" in st or "faulty" in st or broken:
        return "Under repair"
    if spare or "inactive" in st or "notinuse" in st:
        return "In stock"
    return "In use"


def map_header(cells):
    mapping, others, taken = {}, [], set()
    names = [norm(c) for c in cells]
    for field, syns in HEADERS.items():
        for syn in syns:
            hit = next((i for i, h in enumerate(names) if i not in taken and h == syn), None)
            if hit is not None:
                mapping[field] = hit
                taken.add(hit)
                break
    for i, h in enumerate(names):
        if h and i not in taken and h.replace(" ", "").replace(".", "") not in {"sno", "slno", "srno", "#", "no", "sl"}:
            others.append((i, str(cells[i]).strip()))
    return mapping, others


def _date(v):
    if isinstance(v, dt.date):
        return v
    if not v:
        return None
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(str(v), fmt).date()
        except ValueError:
            pass
    return None


def parse_sheet(title, rows):
    """Return (assets, problem). Each asset is a dict of asset columns."""
    rows = [tuple(r) for r in rows if r is not None]
    head_i, mapping, others = None, {}, []
    for i, r in enumerate(rows[:15]):
        m, o = map_header(list(r))
        if len(m) >= 3:
            head_i, mapping, others = i, m, o
            break
    if head_i is None:
        return [], "no header row found (expected columns like Brand, Model, Serial Number)"
    out = []
    for r in rows[head_i + 1:]:
        r = list(r) + [None] * 60
        g = lambda f: clean(r[mapping[f]]) if f in mapping else None
        type_text = g("category")
        core = [type_text, g("make"), g("model"), g("serial_no"), g("hostname"), g("assigned_to")]
        if not any(core):
            continue
        cat = category_for(type_text, title)
        if cat in ("Desktop", "Other") and "server" in f"{g('os') or ''} {g('assigned_to') or ''}".lower():
            cat = "Server"
        specs = " / ".join(str(x) for x in (g("cpu"), g("ram"), g("disk")) if x)
        notes = []
        rem = g("notes")
        if rem and rem.lower() not in ("working", "ok", "good", "active"):
            notes.append(str(rem))
        status = status_for(g("status"), rem)
        st_raw = g("status")
        if st_raw and st_raw.lower() not in ("active", "in use", "working"):
            notes.append(f"Status in sheet: {st_raw}")
        for i, label in others:
            v = clean(r[i])
            if v:
                notes.append(f"{label}: {v}")
        name = g("name") or (str(type_text) if type_text and str(type_text).lower() != cat.lower() else None)
        cost = g("cost")
        try:
            cost = float(str(cost).replace(",", "").replace("₹", "")) if cost else None
        except ValueError:
            notes.append(f"Cost: {cost}")
            cost = None
        out.append({
            "category": cat, "status": status, "name": name,
            "make": g("make"), "model": g("model"), "serial_no": g("serial_no"),
            "hostname": g("hostname"), "ip_address": g("ip_address"), "mac_address": g("mac_address"),
            "os": g("os"), "specs": specs or None, "assigned_to": g("assigned_to"),
            "department": g("department"), "location": g("location"), "vendor": g("vendor"),
            "invoice_no": g("invoice_no"), "purchase_date": _date(g("purchase_date")),
            "warranty_end": _date(g("warranty_end")), "cost": cost,
            "notes": "\n".join(notes) or None,
        })
    for a in out:
        for k, v in a.items():
            if isinstance(v, str):
                a[k] = v[:300]
    return out, None
