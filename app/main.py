import csv
import datetime as dt
import io
import os
from decimal import Decimal, InvalidOperation
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import security as sec
from .config import (ASSET_COLS, ASSET_FIELDS, BRANDS, CATEGORIES, FIELD_LABELS, ROLES,
                     STATUSES, VAULT_KINDS)
from .db import audit, get_conn, init_db, next_asset_code
from .labels import label_preview_svg, labels_pdf, qr_text

BASE = os.path.dirname(__file__)


_db_ready = False


def ensure_db():
    """Create tables / first admin once per server instance (Vercel may skip ASGI lifespan)."""
    global _db_ready
    if not _db_ready:
        init_db(os.environ.get("ADMIN_EMAIL"), os.environ.get("ADMIN_PASSWORD"), sec.hash_password)
        _db_ready = True


app = FastAPI(title="IT Assets & Vault", docs_url=None, redoc_url=None, openapi_url=None)


def setup_problems() -> list[str]:
    """Plain-language checks of the Vercel environment variables (never shows secret values)."""
    problems = []
    for name in ("DATABASE_URL", "SECRET_KEY", "VAULT_KEY"):
        if not os.environ.get(name, "").strip():
            problems.append(f"<b>{name}</b> is missing. Add it in Vercel → Settings → Environment Variables.")
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        if url.startswith("DATABASE_URL=") or url[:1] in "\"'":
            problems.append("<b>DATABASE_URL</b> must start with <code>postgresql://</code> — remove "
                            "<code>DATABASE_URL=</code> and any quote marks from the value.")
        elif not url.startswith(("postgresql://", "postgres://")):
            problems.append("<b>DATABASE_URL</b> must start with <code>postgresql://</code>.")
        elif "****" in url:
            problems.append("<b>DATABASE_URL</b> contains <code>****</code> — in Neon click "
                            "<i>Show password</i> before copying.")
    if os.environ.get("VAULT_KEY", "").strip() and sec.vault_key_problem():
        problems.append("<b>VAULT_KEY</b> is too short. Use a long random value of at least 32 characters.")
    return problems


def _setup_page(problems: list[str]) -> HTMLResponse:
    items = "".join(f"<li style='margin-bottom:10px'>{p}</li>" for p in problems)
    return HTMLResponse(
        "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
        "<title>Setup needed</title><body style='font:15px/1.5 system-ui,sans-serif;max-width:640px;margin:8vh auto;"
        "padding:0 20px;color:#1E1D1D'><h1 style='color:#0A2B66'>IT Assets &amp; Vault — setup needed</h1>"
        f"<ul>{items}</ul><p style='color:#5f6673'>After changing environment variables in Vercel, go to "
        "<b>Deployments → ⋯ → Redeploy</b>. New values only take effect after a redeploy.</p></body>",
        status_code=503)


@app.middleware("http")
async def _setup_and_headers(request: Request, call_next):
    if not request.url.path.startswith("/static") and not _db_ready:
        problems = setup_problems()
        if not problems:
            try:
                ensure_db()
            except Exception as exc:
                msg = str(exc).splitlines()[0] if str(exc) else exc.__class__.__name__
                url = os.environ.get("DATABASE_URL", "")
                pw = url.split("://", 1)[-1].split("@", 1)[0].split(":", 1)[-1] if "@" in url else ""
                if pw:
                    msg = msg.replace(pw, "****")
                problems.append("Could not connect to the Neon database: <code>"
                                + msg.replace("<", "&lt;")[:300] + "</code><br>Check that DATABASE_URL "
                                "is the <i>pooled</i> Neon connection string and the password is correct.")
        if problems:
            return _setup_page(problems)
    resp = await call_next(request)
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    if not request.url.path.startswith("/static"):
        resp.headers.setdefault("Cache-Control", "no-store")
    return resp
app.mount("/static", StaticFiles(directory=os.path.join(BASE, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE, "templates"))
APP_TZ = ZoneInfo(os.environ.get("APP_TZ", "Asia/Kolkata"))
templates.env.filters["local"] = lambda t: t.astimezone(APP_TZ).strftime("%d-%m-%Y %H:%M") if t else ""
templates.env.globals.update(BRANDS=BRANDS, CATEGORIES=CATEGORIES, STATUSES=STATUSES,
                             VAULT_KINDS=VAULT_KINDS, ROLES=ROLES, ASSET_FIELDS=ASSET_FIELDS,
                             FIELD_LABELS=FIELD_LABELS)
SECURE_COOKIE = os.environ.get("COOKIE_SECURE", "1") != "0"


# ---------------------------------------------------------------- auth
class LoginRequired(Exception):
    pass


@app.exception_handler(LoginRequired)
async def _login_redirect(request: Request, exc):
    nxt = quote(str(request.url.path))
    return RedirectResponse(f"/login?next={nxt}", status_code=303)


def current_user(request: Request, conn=Depends(get_conn)):
    data = sec.read_session(request.cookies.get(sec.SESSION_COOKIE))
    if not data:
        raise LoginRequired()
    user = conn.execute("SELECT * FROM users WHERE id=%s AND active", (data["uid"],)).fetchone()
    if not user or user["pw_hash"][-12:] != data.get("pv"):
        raise LoginRequired()
    return user


def require(*roles):
    def dep(user=Depends(current_user)):
        if user["role"] not in roles:
            raise HTTPException(403, "You don't have access to this page.")
        return user
    return dep


can_edit = require("admin", "it")
admin_only = require("admin")


def render(request, name, user=None, **ctx):
    ctx.update(request=request, user=user, msg=request.query_params.get("msg"),
               err=request.query_params.get("err"))
    return templates.TemplateResponse(request, name, ctx)


def back(url, msg=None, err=None):
    if msg:
        url += ("&" if "?" in url else "?") + "msg=" + quote(msg)
    if err:
        url += ("&" if "?" in url else "?") + "err=" + quote(err)
    return RedirectResponse(url, status_code=303)


@app.exception_handler(HTTPException)
async def _http_error(request: Request, exc: HTTPException):
    if request.headers.get("accept", "").startswith("application/json"):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    return templates.TemplateResponse(request, "error.html",
                                      {"request": request, "user": None, "code": exc.status_code,
                                       "detail": exc.detail}, status_code=exc.status_code)


def _setup_open(conn) -> bool:
    """The one-time setup page works only until the first successful sign-in."""
    return conn.execute("SELECT count(*) AS n FROM audit WHERE action='login'").fetchone()["n"] == 0


def _setup_html(error: str = "") -> str:
    err = f'<div class="alert err">{error}</div>' if error else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex,nofollow">
<title>First-time setup · IT Assets & Vault</title><link rel="stylesheet" href="/static/style.css"></head>
<body><main class="wrap"><div class="login card">{err}
<h1>Create the admin account</h1>
<p class="sub">One-time setup. This page switches off for good after the first successful sign-in.</p>
<form method="post" action="/setup" autocomplete="off">
<label class="f">Your name</label><input name="name" required>
<div style="height:10px"></div><label class="f">Email</label><input name="email" type="email" required>
<div style="height:10px"></div><label class="f">Password (min 8 characters)</label>
<input name="password" type="password" minlength="8" required autocomplete="new-password">
<div style="height:10px"></div><label class="f">Confirm password</label>
<input name="confirm" type="password" minlength="8" required autocomplete="new-password">
<div style="height:16px"></div><button class="btn" style="width:100%;justify-content:center">Create admin and sign in</button>
</form></div></main></body></html>"""


@app.get("/setup", response_class=HTMLResponse)
def setup_page(conn=Depends(get_conn)):
    if not _setup_open(conn):
        return back("/login", err="Setup is already complete. Please sign in.")
    return HTMLResponse(_setup_html())


@app.post("/setup")
async def setup_submit(request: Request, conn=Depends(get_conn)):
    if not _setup_open(conn):
        return back("/login", err="Setup is already complete. Please sign in.")
    f = await request.form()
    name = (f.get("name") or "").strip() or "Administrator"
    email = (f.get("email") or "").strip().lower()
    pw = f.get("password") or ""
    if not email or "@" not in email:
        return HTMLResponse(_setup_html("Enter a valid email."), status_code=400)
    if len(pw) < 8 or pw != (f.get("confirm") or ""):
        return HTMLResponse(_setup_html("Passwords must match and be at least 8 characters."), status_code=400)
    conn.execute(
        "INSERT INTO users (email, name, pw_hash, role, active) VALUES (%s,%s,%s,'admin',TRUE) "
        "ON CONFLICT (email) DO UPDATE SET name=EXCLUDED.name, pw_hash=EXCLUDED.pw_hash, role='admin', active=TRUE",
        (email, name, sec.hash_password(pw)))
    user = conn.execute("SELECT * FROM users WHERE email=%s", (email,)).fetchone()
    audit(conn, email, "setup", "user", user["id"], f"Admin account created via setup page: {email}")
    audit(conn, email, "login")
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(sec.SESSION_COOKIE, sec.make_session(user), max_age=sec.SESSION_MAX_AGE,
                    httponly=True, samesite="lax", secure=SECURE_COOKIE)
    return resp


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return render(request, "login.html", next=request.query_params.get("next", "/"))


@app.post("/login")
async def login(request: Request, conn=Depends(get_conn)):
    form = await request.form()
    email = (form.get("email") or "").strip().lower()
    nxt = form.get("next") or "/"
    if not nxt.startswith("/") or nxt.startswith("//"):
        nxt = "/"
    password = form.get("password") or ""
    # Recovery: the ADMIN_EMAIL / ADMIN_PASSWORD set in Vercel always sign in as an active admin.
    env_email = (os.environ.get("ADMIN_EMAIL") or "").strip().lower()
    env_pw = (os.environ.get("ADMIN_PASSWORD") or "").strip()
    # Diagnostic line for Vercel → Logs (shows only yes/no and lengths, never the values)
    print(f"[login-check] typed_email_len={len(email)} admin_email_set={bool(env_email)} "
          f"email_matches_ADMIN_EMAIL={email == env_email} typed_password_len={len(password.strip())} "
          f"ADMIN_PASSWORD_len={len(env_pw)} password_matches={password.strip() == env_pw}", flush=True)
    if env_email and env_pw and email == env_email and password.strip() == env_pw:
        row = conn.execute("SELECT * FROM users WHERE email=%s", (email,)).fetchone()
        if not row or not row["active"] or row["role"] != "admin" or not sec.verify_password(password, row["pw_hash"]):
            conn.execute(
                "INSERT INTO users (email, name, pw_hash, role, active) VALUES (%s, 'Administrator', %s, 'admin', TRUE) "
                "ON CONFLICT (email) DO UPDATE SET pw_hash=EXCLUDED.pw_hash, role='admin', active=TRUE",
                (email, sec.hash_password(password)))
    user = conn.execute("SELECT * FROM users WHERE email=%s AND active", (email,)).fetchone()
    if not user or not sec.verify_password(password, user["pw_hash"]):
        audit(conn, email, "login_failed")
        return back("/login", err="Wrong email or password.")
    audit(conn, email, "login")
    resp = RedirectResponse(nxt, status_code=303)
    resp.set_cookie(sec.SESSION_COOKIE, sec.make_session(user), max_age=sec.SESSION_MAX_AGE,
                    httponly=True, samesite="lax", secure=SECURE_COOKIE)
    return resp


@app.post("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(sec.SESSION_COOKIE)
    return resp


# ---------------------------------------------------------------- dashboard
@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, user=Depends(current_user), conn=Depends(get_conn)):
    q = conn.execute
    stats = {
        "assets": q("SELECT count(*) AS n FROM assets WHERE status NOT IN ('Retired','Lost / Disposed')").fetchone()["n"],
        "in_stock": q("SELECT count(*) AS n FROM assets WHERE status='In stock'").fetchone()["n"],
        "repair": q("SELECT count(*) AS n FROM assets WHERE status='Under repair'").fetchone()["n"],
        "vault": q("SELECT count(*) AS n FROM vault").fetchone()["n"],
    }
    by_company = q("SELECT company, count(*) AS n FROM assets GROUP BY company ORDER BY company").fetchall()
    by_category = q("SELECT category, count(*) AS n FROM assets GROUP BY category ORDER BY n DESC").fetchall()
    warranty = q("SELECT id, asset_code, category, make, model, warranty_end FROM assets "
                 "WHERE warranty_end BETWEEN current_date AND current_date + 60 "
                 "AND status NOT IN ('Retired','Lost / Disposed') ORDER BY warranty_end").fetchall()
    stale = []
    if user["role"] in ("admin", "it"):
        stale = q("SELECT id, title, kind, rotated_on FROM vault WHERE rotated_on IS NULL "
                  "OR rotated_on < current_date - 90 ORDER BY rotated_on NULLS FIRST LIMIT 15").fetchall()
    recent = q("SELECT * FROM audit WHERE entity='asset' AND action <> 'print_label' ORDER BY at DESC LIMIT 8").fetchall()
    return render(request, "dashboard.html", user, stats=stats, by_company=by_company,
                  by_category=by_category, warranty=warranty, stale=stale, recent=recent)


# ---------------------------------------------------------------- assets
def _clean_asset_form(form, existing=None):
    data, errors = {}, []
    for col, label, kind in ASSET_FIELDS:
        val = (form.get(col) or "").strip()
        if kind == "date":
            if val:
                try:
                    val = dt.date.fromisoformat(val)
                except ValueError:
                    errors.append(f"{label}: invalid date")
                    val = None
            else:
                val = None
        elif kind == "number":
            if val:
                try:
                    val = Decimal(val.replace(",", ""))
                except InvalidOperation:
                    errors.append(f"{label}: not a number")
                    val = None
            else:
                val = None
        else:
            val = val or None
        data[col] = val
    if existing:
        data["company"] = existing["company"]  # code prefix is tied to the company
    if data["company"] not in BRANDS:
        errors.append("Company is required")
    if data["category"] not in CATEGORIES:
        errors.append("Category is required")
    if data["status"] not in STATUSES:
        data["status"] = "In use"
    return data, errors


def _suggestions(conn):
    out = {}
    for col in ("make", "department", "location", "assigned_to", "vendor", "os"):
        out[col] = [r["v"] for r in conn.execute(
            f"SELECT DISTINCT {col} AS v FROM assets WHERE {col} IS NOT NULL ORDER BY 1 LIMIT 200").fetchall()]
    return out


def _filters(request):
    p = request.query_params
    where, args = [], []
    if p.get("q"):
        like = f"%{p['q'].strip()}%"
        where.append("(asset_code ILIKE %s OR name ILIKE %s OR make ILIKE %s OR model ILIKE %s OR serial_no ILIKE %s "
                     "OR assigned_to ILIKE %s OR hostname ILIKE %s OR ip_address ILIKE %s OR location ILIKE %s "
                     "OR department ILIKE %s)")
        args += [like] * 10
    for col in ("company", "category", "status"):
        if p.get(col):
            where.append(f"{col} = %s")
            args.append(p[col])
    return ("WHERE " + " AND ".join(where)) if where else "", args


@app.get("/assets", response_class=HTMLResponse)
def asset_list(request: Request, user=Depends(current_user), conn=Depends(get_conn)):
    where, args = _filters(request)
    rows = conn.execute(f"SELECT * FROM assets {where} ORDER BY asset_code", args).fetchall()
    return render(request, "assets.html", user, rows=rows, f=request.query_params)


@app.get("/assets/export.csv")
def asset_export(request: Request, user=Depends(current_user), conn=Depends(get_conn)):
    where, args = _filters(request)
    rows = conn.execute(f"SELECT * FROM assets {where} ORDER BY asset_code", args).fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Asset code"] + [FIELD_LABELS[c] for c in ASSET_COLS])
    for r in rows:
        w.writerow([r["asset_code"]] + ["" if r[c] is None else r[c] for c in ASSET_COLS])
    audit(conn, user["email"], "export_assets", "asset", None, f"{len(rows)} rows")
    return Response("﻿" + buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="it-assets-{dt.date.today()}.csv"'})


@app.get("/assets/new", response_class=HTMLResponse)
def asset_new(request: Request, user=Depends(can_edit), conn=Depends(get_conn)):
    blank = {c: None for c in ASSET_COLS}
    blank.update(status="In use", company=request.query_params.get("company", ""))
    return render(request, "asset_form.html", user, a=blank, sugg=_suggestions(conn), errors=[])


@app.post("/assets/new")
async def asset_create(request: Request, user=Depends(can_edit), conn=Depends(get_conn)):
    form = await request.form()
    data, errors = _clean_asset_form(form)
    if errors:
        return render(request, "asset_form.html", user, a=data, sugg=_suggestions(conn), errors=errors)
    code = next_asset_code(conn, BRANDS[data["company"]]["prefix"])
    cols = ["asset_code"] + ASSET_COLS + ["created_by", "updated_by"]
    vals = [code] + [data[c] for c in ASSET_COLS] + [user["email"], user["email"]]
    row = conn.execute(f"INSERT INTO assets ({','.join(cols)}) VALUES ({','.join(['%s'] * len(cols))}) RETURNING id",
                       vals).fetchone()
    audit(conn, user["email"], "create", "asset", row["id"], f"Created {code}")
    return back(f"/assets/{row['id']}", msg=f"{code} created — print its label below.")


def _get_asset(conn, aid):
    a = conn.execute("SELECT * FROM assets WHERE id=%s", (aid,)).fetchone()
    if not a:
        raise HTTPException(404, "Asset not found")
    return a


@app.get("/assets/{aid}", response_class=HTMLResponse)
def asset_detail(aid: int, request: Request, user=Depends(current_user), conn=Depends(get_conn)):
    a = _get_asset(conn, aid)
    history = conn.execute("SELECT * FROM audit WHERE entity='asset' AND entity_id=%s ORDER BY at DESC",
                           (aid,)).fetchall()
    linked = []
    if user["role"] in ("admin", "it"):
        linked = conn.execute("SELECT id, title, kind, username FROM vault WHERE asset_id=%s ORDER BY title",
                              (aid,)).fetchall()
    return render(request, "asset_detail.html", user, a=a, history=history, linked=linked, qr=qr_text(a),
                  preview=label_preview_svg(a, _label_style(request)), style=_label_style(request))


@app.get("/assets/{aid}/edit", response_class=HTMLResponse)
def asset_edit(aid: int, request: Request, user=Depends(can_edit), conn=Depends(get_conn)):
    return render(request, "asset_form.html", user, a=_get_asset(conn, aid), sugg=_suggestions(conn), errors=[])


@app.post("/assets/{aid}/edit")
async def asset_update(aid: int, request: Request, user=Depends(can_edit), conn=Depends(get_conn)):
    old = _get_asset(conn, aid)
    form = await request.form()
    data, errors = _clean_asset_form(form, existing=old)
    if errors:
        data.update(id=aid, asset_code=old["asset_code"])
        return render(request, "asset_form.html", user, a=data, sugg=_suggestions(conn), errors=errors)
    changes = []
    for c in ASSET_COLS:
        ov, nv = old[c], data[c]
        if isinstance(ov, Decimal) and nv is not None:
            same = ov == nv
        else:
            same = (ov or None) == (nv or None)
        if not same:
            changes.append(f"{FIELD_LABELS[c]}: {ov if ov is not None else '—'} → {nv if nv is not None else '—'}")
    if not changes:
        return back(f"/assets/{aid}", msg="No changes.")
    sets = ",".join(f"{c}=%s" for c in ASSET_COLS)
    conn.execute(f"UPDATE assets SET {sets}, updated_by=%s, updated_at=now() WHERE id=%s",
                 [data[c] for c in ASSET_COLS] + [user["email"], aid])
    audit(conn, user["email"], "update", "asset", aid, "\n".join(changes))
    return back(f"/assets/{aid}", msg="Saved.")


@app.post("/assets/{aid}/delete")
def asset_delete(aid: int, user=Depends(admin_only), conn=Depends(get_conn)):
    a = _get_asset(conn, aid)
    conn.execute("DELETE FROM assets WHERE id=%s", (aid,))
    audit(conn, user["email"], "delete", "asset", aid, f"Deleted {a['asset_code']}")
    return back("/assets", msg=f"{a['asset_code']} deleted.")


def _label_style(request):
    return "classic" if request.query_params.get("style") == "classic" else "logo"


@app.get("/assets/{aid}/label.pdf")
def asset_label(aid: int, request: Request, user=Depends(current_user), conn=Depends(get_conn)):
    a = _get_asset(conn, aid)
    layout = "a4" if request.query_params.get("layout") == "a4" else "roll"
    audit(conn, user["email"], "print_label", "asset", aid, f"Label ({layout}, {_label_style(request)})")
    return Response(labels_pdf([a], layout, _label_style(request)), media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{a["asset_code"]}-label.pdf"'})


@app.get("/labels.pdf")
def bulk_labels(request: Request, user=Depends(current_user), conn=Depends(get_conn)):
    ids = [int(x) for x in request.query_params.getlist("id") if x.isdigit()]
    if not ids:
        raise HTTPException(400, "Select at least one asset.")
    rows = conn.execute("SELECT * FROM assets WHERE id = ANY(%s) ORDER BY asset_code", (ids,)).fetchall()
    layout = "a4" if request.query_params.get("layout") == "a4" else "roll"
    for r in rows:
        audit(conn, user["email"], "print_label", "asset", r["id"], f"Label ({layout}, bulk)")
    return Response(labels_pdf(rows, layout, _label_style(request)), media_type="application/pdf",
                    headers={"Content-Disposition": 'inline; filename="asset-labels.pdf"'})


# ---------------------------------------------------------------- password vault
def _vault_groups(conn):
    return conn.execute("SELECT kind AS name, count(*) AS n FROM vault GROUP BY kind ORDER BY lower(kind)").fetchall()


@app.get("/vault", response_class=HTMLResponse)
def vault_list(request: Request, user=Depends(can_edit), conn=Depends(get_conn)):
    p = request.query_params
    groups = _vault_groups(conn)
    group = p.get("g") or ""
    q = (p.get("q") or "").strip()
    where, args = [], []
    if q:
        like = f"%{q}%"
        where.append("(v.title ILIKE %s OR v.url_host ILIKE %s OR v.username ILIKE %s OR a.asset_code ILIKE %s "
                     "OR v.kind ILIKE %s)")
        args += [like] * 5
    elif group:
        where.append("v.kind = %s")
        args.append(group)
    elif groups:
        group = groups[0]["name"]          # open the first group by default
        where.append("v.kind = %s")
        args.append(group)
    sql = ("SELECT v.id, v.title, v.kind, v.company, v.url_host, v.username, v.rotated_on, v.updated_at, "
           "v.secret_enc IS NOT NULL AS has_secret, v.notes_enc IS NOT NULL AS has_notes, "
           "a.id AS asset_id, a.asset_code "
           "FROM vault v LEFT JOIN assets a ON a.id = v.asset_id "
           + ("WHERE " + " AND ".join(where) if where else "") + " ORDER BY lower(v.title), v.id")
    rows = conn.execute(sql, args).fetchall()
    return render(request, "vault.html", user, rows=rows, groups=groups, group=group, q=q,
                  total=sum(g["n"] for g in groups), today=dt.date.today())


@app.post("/vault/reveal-group")
async def vault_reveal_group(request: Request, user=Depends(can_edit), conn=Depends(get_conn)):
    form = await request.form()
    group = (form.get("g") or "").strip()
    rows = conn.execute("SELECT id, secret_enc, notes_enc FROM vault WHERE kind=%s", (group,)).fetchall()
    audit(conn, user["email"], "reveal", "vault", None, f"Viewed all {len(rows)} passwords in group '{group}'")
    return JSONResponse({str(r["id"]): {"s": sec.decrypt(r["secret_enc"]), "n": sec.decrypt(r["notes_enc"])}
                         for r in rows}, headers={"Cache-Control": "no-store"})


# ---- Excel / CSV import: every sheet becomes a group (tab) ----
_COLMAP = {
    "secret": ["password", "passwd", "pwd", "pass", "passcode", "pass code", "pin", "key", "wifi password",
               "wi-fi password", "licence key", "license key", "product key", "security key"],
    "username": ["username", "user name", "user id", "userid", "user", "login", "login id", "login name",
                 "mail id", "email id", "email", "e-mail", "mail", "account id"],
    "url_host": ["url", "link", "website", "web site", "portal", "ip address", "ip", "host", "hostname",
                 "server", "address", "domain"],
    "title": ["name", "title", "description", "system", "software", "application", "app", "account",
              "ssid", "wifi name", "wi-fi name", "network name", "device", "site", "particulars", "purpose",
              "service", "module", "location", "company name"],
}
_SERIAL_COLS = {"sno", "slno", "srno", "serialno", "#", "no", "sl", "s"}


def _norm(h):
    return " ".join(str(h or "").strip().lower().replace("_", " ").replace(".", " ").split())


def _map_header(cells):
    """Return ({field: column_index}, [(column_index, label) for other columns]) for a header row."""
    mapping, taken = {}, set()
    norm = [_norm(c) for c in cells]
    for field in ("secret", "username", "url_host", "title"):
        for syn in _COLMAP[field]:                       # exact header match first
            hit = next((i for i, h in enumerate(norm) if i not in taken and h == syn), None)
            if hit is not None:
                mapping[field] = hit
                taken.add(hit)
                break
        if field not in mapping:                          # then "contains" match
            hit = next((i for i, h in enumerate(norm) if i not in taken and h
                        and any(syn in h for syn in _COLMAP[field] if len(syn) > 3)), None)
            if hit is not None:
                mapping[field] = hit
                taken.add(hit)
    others = [(i, str(cells[i]).strip()) for i, h in enumerate(norm)
              if h and i not in taken and h.replace(" ", "") not in _SERIAL_COLS]
    return mapping, others


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if isinstance(v, (dt.datetime, dt.date)):
        return v.strftime("%d-%m-%Y")
    return str(v).strip()


def _parse_sheet(name, rows):
    """Find the header row in the first 15 rows and return entry dicts (or a reason it was skipped)."""
    rows = [r for r in rows if r is not None]
    header_i, mapping, others = None, {}, []
    for i, r in enumerate(rows[:15]):
        m, o = _map_header(list(r))
        if "secret" in m or len(m) >= 2:
            header_i, mapping, others = i, m, o
            break
    if header_i is None:
        return [], "no header row with a Password or Username column found"
    out = []
    for n, r in enumerate(rows[header_i + 1:], start=header_i + 2):
        r = list(r) + [None] * 50
        get = lambda f: _cell(r[mapping[f]]) if f in mapping else ""
        title, user_, secret, host = get("title"), get("username"), get("secret"), get("url_host")
        extra = [f"{label}: {_cell(r[i])}" for i, label in others if _cell(r[i])]
        if not (title or user_ or secret or host):
            continue
        if not title:
            title = extra[0].split(": ", 1)[1] if extra else (user_ or host or f"{name} row {n}")
        out.append({"title": title[:200], "username": user_[:200] or None, "secret": secret,
                    "url_host": host[:300] or None, "notes": "\n".join(extra)})
    return out, None


@app.get("/vault/import", response_class=HTMLResponse)
def vault_import_page(request: Request, user=Depends(can_edit)):
    return render(request, "vault_import.html", user, result=None)


@app.post("/vault/import", response_class=HTMLResponse)
async def vault_import(request: Request, user=Depends(can_edit), conn=Depends(get_conn)):
    form = await request.form()
    up = form.get("file")
    company = form.get("company") if form.get("company") in BRANDS else None
    if not up or not getattr(up, "filename", ""):
        return render(request, "vault_import.html", user, result=None, error="Choose an Excel (.xlsx) or CSV file.")
    data = await up.read()
    fname = os.path.basename(up.filename)
    sheets = []
    try:
        if fname.lower().endswith(".csv"):
            text = data.decode("utf-8-sig", errors="replace")
            sheets.append((os.path.splitext(fname)[0], list(csv.reader(io.StringIO(text)))))
        elif fname.lower().endswith((".xlsx", ".xlsm")):
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            for ws in wb.worksheets:
                if ws.sheet_state != "visible":
                    continue
                sheets.append((ws.title.strip()[:80], [tuple(r) for r in ws.iter_rows(values_only=True)]))
        else:
            return render(request, "vault_import.html", user, result=None,
                          error="Only .xlsx or .csv files can be imported. In Excel use File → Save As → Excel Workbook (.xlsx).")
    except Exception as exc:
        return render(request, "vault_import.html", user, result=None, error=f"Could not read the file: {exc}")

    result = []
    for name, rows in sheets:
        entries, problem = _parse_sheet(name, rows)
        added = skipped = 0
        for e in entries:
            dup = conn.execute("SELECT 1 FROM vault WHERE kind=%s AND title=%s AND coalesce(username,'')=%s",
                               (name, e["title"], e["username"] or "")).fetchone()
            if dup:
                skipped += 1
                continue
            conn.execute(
                "INSERT INTO vault (title, kind, company, url_host, username, secret_enc, notes_enc, rotated_on, "
                "created_by, updated_by) VALUES (%s,%s,%s,%s,%s,%s,%s,NULL,%s,%s)",
                (e["title"], name, company, e["url_host"], e["username"], sec.encrypt(e["secret"]),
                 sec.encrypt(e["notes"]), user["email"], user["email"]))
            added += 1
        result.append({"name": name, "added": added, "skipped": skipped, "problem": problem})
    total = sum(r["added"] for r in result)
    audit(conn, user["email"], "import", "vault", None,
          f"Imported {total} entries from '{fname}': " + ", ".join(f"{r['name']} ({r['added']})" for r in result))
    return render(request, "vault_import.html", user, result=result, total=total, fname=fname)


def _asset_options(conn):
    return conn.execute("SELECT id, asset_code, category, make, model FROM assets ORDER BY asset_code").fetchall()


@app.get("/vault/new", response_class=HTMLResponse)
def vault_new(request: Request, user=Depends(can_edit), conn=Depends(get_conn)):
    aid = request.query_params.get("asset_id")
    v = {"id": None, "title": "", "kind": request.query_params.get("g", ""), "company": "", "url_host": "",
         "username": "", "notes": "", "asset_id": int(aid) if aid and aid.isdigit() else None, "rotated_on": None}
    return render(request, "vault_form.html", user, v=v, assets=_asset_options(conn), errors=[],
                  group_names=_group_names(conn))


def _group_names(conn):
    names = [g["name"] for g in _vault_groups(conn)]
    return names + [k for k in VAULT_KINDS if k not in names]


def _clean_vault_form(form):
    data = {k: (form.get(k) or "").strip() or None for k in ("title", "kind", "company", "url_host", "username")}
    data["asset_id"] = int(form["asset_id"]) if (form.get("asset_id") or "").isdigit() else None
    errors = []
    if not data["title"]:
        errors.append("Title is required")
    if not data["kind"]:
        errors.append("Group is required (e.g. SAP credentials, Wi-Fi credentials)")
    elif len(data["kind"]) > 80:
        errors.append("Group name is too long")
    if data["company"] and data["company"] not in BRANDS:
        data["company"] = None
    return data, errors


@app.post("/vault/new")
async def vault_create(request: Request, user=Depends(can_edit), conn=Depends(get_conn)):
    form = await request.form()
    data, errors = _clean_vault_form(form)
    if errors:
        data.update(id=None, notes=form.get("notes", ""), rotated_on=None)
        return render(request, "vault_form.html", user, group_names=_group_names(conn), v=data, assets=_asset_options(conn), errors=errors)
    secret = form.get("secret") or ""
    row = conn.execute(
        "INSERT INTO vault (title, kind, company, url_host, username, secret_enc, notes_enc, asset_id, rotated_on, "
        "created_by, updated_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (data["title"], data["kind"], data["company"], data["url_host"], data["username"], sec.encrypt(secret),
         sec.encrypt(form.get("notes") or ""), data["asset_id"], dt.date.today() if secret else None,
         user["email"], user["email"])).fetchone()
    audit(conn, user["email"], "create", "vault", row["id"], f"Created entry '{data['title']}'")
    return back(f"/vault?g={quote(data['kind'])}", msg=f"Saved '{data['title']}'.")


def _get_vault(conn, vid):
    v = conn.execute("SELECT * FROM vault WHERE id=%s", (vid,)).fetchone()
    if not v:
        raise HTTPException(404, "Entry not found")
    return v


@app.get("/vault/{vid}/edit", response_class=HTMLResponse)
def vault_edit(vid: int, request: Request, user=Depends(can_edit), conn=Depends(get_conn)):
    v = dict(_get_vault(conn, vid))
    v["notes"] = sec.decrypt(v["notes_enc"])
    hist = conn.execute("SELECT * FROM audit WHERE entity='vault' AND entity_id=%s ORDER BY at DESC LIMIT 50",
                        (vid,)).fetchall()
    return render(request, "vault_form.html", user, v=v, assets=_asset_options(conn), errors=[], history=hist,
                  group_names=_group_names(conn))


@app.post("/vault/{vid}/edit")
async def vault_update(vid: int, request: Request, user=Depends(can_edit), conn=Depends(get_conn)):
    old = _get_vault(conn, vid)
    form = await request.form()
    data, errors = _clean_vault_form(form)
    if errors:
        data.update(id=vid, notes=form.get("notes", ""), rotated_on=old["rotated_on"])
        return render(request, "vault_form.html", user, group_names=_group_names(conn), v=data, assets=_asset_options(conn), errors=errors)
    secret = form.get("secret") or ""
    changed = [k for k in ("title", "kind", "company", "url_host", "username", "asset_id") if old[k] != data[k]]
    if (form.get("notes") or "") != sec.decrypt(old["notes_enc"]):
        changed.append("notes")
    secret_enc, rotated = old["secret_enc"], old["rotated_on"]
    if secret:
        if secret != sec.decrypt(old["secret_enc"]):
            changed.append("password")
            rotated = dt.date.today()
        secret_enc = sec.encrypt(secret)
    if not changed:
        return back(f"/vault?g={quote(data['kind'] or '')}", msg="No changes.")
    conn.execute("UPDATE vault SET title=%s, kind=%s, company=%s, url_host=%s, username=%s, secret_enc=%s, "
                 "notes_enc=%s, asset_id=%s, rotated_on=%s, updated_by=%s, updated_at=now() WHERE id=%s",
                 (data["title"], data["kind"], data["company"], data["url_host"], data["username"], secret_enc,
                  sec.encrypt(form.get("notes") or ""), data["asset_id"], rotated, user["email"], vid))
    audit(conn, user["email"], "update", "vault", vid, "Changed: " + ", ".join(changed))
    return back(f"/vault?g={quote(data['kind'])}", msg=f"Updated '{data['title']}'.")


@app.post("/vault/{vid}/reveal")
def vault_reveal(vid: int, user=Depends(can_edit), conn=Depends(get_conn)):
    v = _get_vault(conn, vid)
    audit(conn, user["email"], "reveal", "vault", vid, f"Viewed password of '{v['title']}'")
    return JSONResponse({"secret": sec.decrypt(v["secret_enc"])}, headers={"Cache-Control": "no-store"})


@app.post("/vault/{vid}/delete")
def vault_delete(vid: int, user=Depends(admin_only), conn=Depends(get_conn)):
    v = _get_vault(conn, vid)
    conn.execute("DELETE FROM vault WHERE id=%s", (vid,))
    audit(conn, user["email"], "delete", "vault", vid, f"Deleted entry '{v['title']}'")
    return back(f"/vault?g={quote(v['kind'])}", msg=f"Deleted '{v['title']}'.")


# ---------------------------------------------------------------- users & audit
@app.get("/users", response_class=HTMLResponse)
def users_page(request: Request, user=Depends(admin_only), conn=Depends(get_conn)):
    rows = conn.execute("SELECT id, email, name, role, active, created_at FROM users ORDER BY name").fetchall()
    return render(request, "users.html", user, rows=rows)


@app.post("/users/new")
async def users_create(request: Request, user=Depends(admin_only), conn=Depends(get_conn)):
    f = await request.form()
    email, name, role, pw = (f.get("email") or "").strip().lower(), (f.get("name") or "").strip(), f.get("role"), f.get("password") or ""
    if not email or not name or role not in ROLES or len(pw) < 8:
        return back("/users", err="Fill name, email, role and a password of at least 8 characters.")
    if conn.execute("SELECT 1 FROM users WHERE email=%s", (email,)).fetchone():
        return back("/users", err=f"{email} already exists.")
    row = conn.execute("INSERT INTO users (email, name, pw_hash, role) VALUES (%s,%s,%s,%s) RETURNING id",
                       (email, name, sec.hash_password(pw), role)).fetchone()
    audit(conn, user["email"], "create", "user", row["id"], f"Added {email} as {role}")
    return back("/users", msg=f"Added {name}.")


@app.post("/users/{uid}")
async def users_update(uid: int, request: Request, user=Depends(admin_only), conn=Depends(get_conn)):
    f = await request.form()
    target = conn.execute("SELECT * FROM users WHERE id=%s", (uid,)).fetchone()
    if not target:
        raise HTTPException(404, "User not found")
    role = f.get("role") if f.get("role") in ROLES else target["role"]
    active = f.get("active") == "on"
    if uid == user["id"] and (role != "admin" or not active):
        return back("/users", err="You can't remove your own admin access.")
    conn.execute("UPDATE users SET role=%s, active=%s WHERE id=%s", (role, active, uid))
    detail = f"{target['email']}: role {role}, {'active' if active else 'disabled'}"
    pw = f.get("password") or ""
    if pw:
        if len(pw) < 8:
            return back("/users", err="New password must be at least 8 characters.")
        conn.execute("UPDATE users SET pw_hash=%s WHERE id=%s", (sec.hash_password(pw), uid))
        detail += ", password reset"
    audit(conn, user["email"], "update", "user", uid, detail)
    return back("/users", msg="User updated.")


@app.get("/audit", response_class=HTMLResponse)
def audit_page(request: Request, user=Depends(admin_only), conn=Depends(get_conn)):
    p = request.query_params
    where, args = [], []
    if p.get("q"):
        where.append("(user_email ILIKE %s OR detail ILIKE %s)")
        args += [f"%{p['q']}%"] * 2
    if p.get("action"):
        where.append("action=%s")
        args.append(p["action"])
    rows = conn.execute("SELECT * FROM audit " + ("WHERE " + " AND ".join(where) if where else "")
                        + " ORDER BY at DESC LIMIT 500", args).fetchall()
    return render(request, "audit.html", user, rows=rows, f=p)


@app.get("/account", response_class=HTMLResponse)
def account(request: Request, user=Depends(current_user)):
    return render(request, "account.html", user)


@app.post("/account/password")
async def account_password(request: Request, user=Depends(current_user), conn=Depends(get_conn)):
    f = await request.form()
    if not sec.verify_password(f.get("current") or "", user["pw_hash"]):
        return back("/account", err="Current password is wrong.")
    new = f.get("new") or ""
    if len(new) < 8 or new != f.get("confirm"):
        return back("/account", err="New passwords must match and be at least 8 characters.")
    new_hash = sec.hash_password(new)
    conn.execute("UPDATE users SET pw_hash=%s WHERE id=%s", (new_hash, user["id"]))
    audit(conn, user["email"], "change_password", "user", user["id"])
    resp = back("/account", msg="Password changed.")
    resp.set_cookie(sec.SESSION_COOKIE, sec.make_session({**user, "pw_hash": new_hash}),
                    max_age=sec.SESSION_MAX_AGE, httponly=True, samesite="lax", secure=SECURE_COOKIE)
    return resp
