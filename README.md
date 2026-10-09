# IT Assets & Vault — Bharat Steel Group

A small web app to keep a register of all IT assets and the passwords for software
and systems, with 2 × 2 inch branded QR labels for every asset.

Stack: FastAPI + Jinja (server-rendered) · Neon Postgres · Vercel. Same pattern as the
Metfraa Portal and Work Tracker.

## What it does

**Assets**
- Add / edit assets for Bharat Steel, Metfraa or Crayon. The asset code is generated
  automatically per company: `BSC-IT-0001`, `MET-IT-0001`, `CRS-IT-0001`.
- Fields: category, status, make, model, serial no., hostname, IP, MAC, OS, specs,
  assigned to, department, location, vendor, invoice no., purchase date, warranty end,
  cost, notes. Department / location / make etc. auto-suggest from earlier entries.
- Search and filter by company, category, status; export to CSV (opens in Excel).
- Full change history per asset ("Assigned to: R. Karthik → S. Priya").

**QR labels (2 × 2 in)**
- Company logo (or name) on top, QR code in the middle, asset code and type/model below.
- Scanning the QR with any phone camera shows the asset details as text — works
  offline, no login needed. **Passwords are never put on a label.**
- Print one label, or tick many assets and print in bulk:
  - *Label printer* — one 2 × 2 in label per PDF page (thermal / sticker printers)
  - *A4 sheet* — 15 labels per page with light cut lines
- Print at **100% / Actual size** (not "Fit to page").
- Tested to scan when printed at 203 dpi and 300 dpi. If an asset has very long text,
  the least important lines are dropped from the QR automatically so it stays scannable.

**Password vault**
- Software logins, server/system passwords, network devices, email, Wi-Fi, licence keys…
- Passwords **and notes** are encrypted in the database (Fernet, AES + HMAC) with
  `VAULT_KEY`; the database alone is useless without the key.
- Passwords are masked; *Show* hides again after 30 s; *Copy* copies to clipboard.
  Every show/copy is recorded with user and time.
- Link a password to an asset (e.g. router admin, NVR, BIOS) — it appears on the asset page.
- Built-in strong password generator. Dashboard lists passwords not changed in 90+ days.

**Users & roles**

| Role   | Assets | Labels | Password vault | Users & activity log |
|--------|--------|--------|----------------|----------------------|
| ADMIN  | edit + delete | ✔ | ✔ (+ delete) | ✔ |
| IT     | edit   | ✔      | ✔              | — |
| VIEWER | view   | ✔      | —              | — |

Logins lock for 15 minutes after 5 wrong passwords. Changing a password signs that
user out everywhere else. Activity log records logins, edits, label prints, exports
and every password reveal.

## Deploy (Vercel + Neon) — about 15 minutes

1. **Database** — in Neon create a project/database (e.g. `itvault`). Copy the
   *pooled* connection string (`...-pooler...neon.tech/itvault?sslmode=require`).
   Tables are created automatically on first visit.

2. **Generate the two keys** (any machine with Python):
   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(48))"            # SECRET_KEY
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"   # VAULT_KEY
   ```
   ⚠️ **Store VAULT_KEY somewhere safe outside the app** (e.g. sealed with the MD /
   in the company safe). If it is lost or changed, saved passwords cannot be decrypted.

3. **Push to GitHub** under the BSC23609 org (private repo), then import it in Vercel
   (framework: *Other*). Add these Environment Variables:

   | Variable | Value |
   |----------|-------|
   | `DATABASE_URL` | Neon pooled connection string |
   | `SECRET_KEY` | from step 2 |
   | `VAULT_KEY` | from step 2 |
   | `ADMIN_EMAIL` | first admin login, e.g. `jeeva@bharatsteels.in` |
   | `ADMIN_PASSWORD` | temporary password — change it after first login |
   | `LABEL_CONTACT` | optional line added to the QR text, e.g. `IT Helpdesk: 044 6791 7800` |
   | `APP_TZ` | optional, default `Asia/Kolkata` |

   The first admin is created only when the users table is empty. After that,
   you can remove `ADMIN_PASSWORD` from Vercel.

4. **Logos** — put transparent PNG logos in `app/static/brand/`:
   `bsc.png` (full Bharat Steel lockup), `metfraa.png`, `crayon.png`. Wide/horizontal
   logos fit best. Without a file, the label prints the company name in navy text.

5. Deploy, open the URL, sign in, add users under **Users**.
   Optional: map a subdomain such as `it.bharatsteels.in` in Vercel → Domains.

## Run locally

```bash
pip install -r requirements.txt
cp .env.example .env    # fill in values, then:
set -a; . ./.env; set +a
COOKIE_SECURE=0 uvicorn app.main:app --reload
```

## Customising

`app/config.py` holds the company names, asset-code prefixes, label colours, logo file
names, and the dropdown lists (categories, statuses, password types). Label layout is
in `app/labels.py`.

## Backups

Neon keeps point-in-time history. For an extra copy, use **Assets → Export CSV**
regularly. Vault entries stay encrypted in any database backup.
