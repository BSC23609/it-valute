"""Company branding, dropdown lists and asset field definitions.

Edit this file to change label branding, asset-code prefixes or dropdown values.
"""
import os

# Company branding used on QR labels. Put logo PNGs (transparent background,
# wide lockup) in app/static/brand/ with the file names below. If a logo file is
# missing, the label falls back to the company name in text.
BRANDS = {
    "BSC": {
        "name": "Bharat Steel (Chennai) Pvt. Ltd.",
        "short": "BHARAT STEEL",
        "prefix": "BSC",          # asset codes: BSC-IT-0001
        "color": "#0A2B66",
        "logo": "bsc.png",
    },
    "METFRAA": {
        "name": "Metfraa Steel Buildings Pvt. Ltd.",
        "short": "METFRAA",
        "prefix": "MET",
        "color": "#0A2B66",
        "logo": "metfraa.png",
    },
    "CRAYON": {
        "name": "Crayon Roofings & Structures",
        "short": "CRAYON ROOFINGS",
        "prefix": "CRS",
        "color": "#002654",
        "logo": "crayon.png",
    },
}

# Optional one-line contact printed at the bottom of the QR text, e.g. "IT Helpdesk: 044 6791 7800"
LABEL_CONTACT = os.environ.get("LABEL_CONTACT", "").strip()

CATEGORIES = [
    "Desktop", "Laptop", "Monitor", "Server", "Printer / Scanner", "Network switch",
    "Router / Firewall", "Wi-Fi access point", "UPS", "CCTV / NVR", "Biometric device",
    "Mobile / Tablet", "Storage / NAS", "Projector / Display", "Phone / EPABX", "Other",
]

STATUSES = ["In use", "In stock", "Under repair", "Retired", "Lost / Disposed"]

VAULT_KINDS = [
    "Software login", "Software licence key", "Server / System", "Network device",
    "Email account", "Cloud / SaaS", "Database", "Wi-Fi", "CCTV / Device", "Other",
]

ROLES = {
    "admin": "Admin — everything, including users",
    "it": "IT — assets and password vault",
    "viewer": "Viewer — view assets and print labels only",
}

# (column, label, input type). Drives the form, detail page, CSV export and change history.
ASSET_FIELDS = [
    ("company", "Company", "company"),
    ("category", "Category", "category"),
    ("status", "Status", "status"),
    ("name", "Asset name / description", "text"),
    ("make", "Make", "text"),
    ("model", "Model", "text"),
    ("serial_no", "Serial no.", "text"),
    ("hostname", "Hostname", "text"),
    ("ip_address", "IP address", "text"),
    ("mac_address", "MAC address", "text"),
    ("os", "Operating system", "text"),
    ("specs", "Specs (CPU / RAM / storage)", "text"),
    ("assigned_to", "Assigned to", "text"),
    ("department", "Department", "text"),
    ("location", "Location", "text"),
    ("vendor", "Vendor", "text"),
    ("invoice_no", "Invoice no.", "text"),
    ("purchase_date", "Purchase date", "date"),
    ("warranty_end", "Warranty end", "date"),
    ("cost", "Cost (₹)", "number"),
    ("notes", "Notes", "textarea"),
]
ASSET_COLS = [f[0] for f in ASSET_FIELDS]
FIELD_LABELS = {f[0]: f[1] for f in ASSET_FIELDS}
