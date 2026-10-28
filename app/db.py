import os

import psycopg
from psycopg.rows import dict_row

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          SERIAL PRIMARY KEY,
    email       TEXT UNIQUE NOT NULL,
    name        TEXT NOT NULL,
    pw_hash     TEXT NOT NULL,
    role        TEXT NOT NULL DEFAULT 'viewer' CHECK (role IN ('admin','it','viewer')),
    active      BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS code_counters (
    prefix TEXT PRIMARY KEY,
    last   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS assets (
    id            SERIAL PRIMARY KEY,
    asset_code    TEXT UNIQUE NOT NULL,
    company       TEXT NOT NULL,
    category      TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'In use',
    name          TEXT,
    make          TEXT,
    model         TEXT,
    serial_no     TEXT,
    hostname      TEXT,
    ip_address    TEXT,
    mac_address   TEXT,
    os            TEXT,
    specs         TEXT,
    assigned_to   TEXT,
    department    TEXT,
    location      TEXT,
    vendor        TEXT,
    invoice_no    TEXT,
    purchase_date DATE,
    warranty_end  DATE,
    cost          NUMERIC(12,2),
    notes         TEXT,
    created_by    TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by    TEXT,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS vault (
    id          SERIAL PRIMARY KEY,
    title       TEXT NOT NULL,
    kind        TEXT NOT NULL,
    company     TEXT,
    url_host    TEXT,
    username    TEXT,
    secret_enc  TEXT,
    notes_enc   TEXT,
    asset_id    INTEGER REFERENCES assets(id) ON DELETE SET NULL,
    rotated_on  DATE,
    created_by  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by  TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS asset_files (
    id           SERIAL PRIMARY KEY,
    asset_id     INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    kind         TEXT NOT NULL DEFAULT 'invoice',
    filename     TEXT NOT NULL,
    content_type TEXT,
    size         INTEGER,
    data         BYTEA,
    onedrive_url TEXT,
    onedrive_note TEXT,
    uploaded_by  TEXT,
    uploaded_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS asset_files_asset_idx ON asset_files(asset_id);

CREATE TABLE IF NOT EXISTS audit (
    id         BIGSERIAL PRIMARY KEY,
    at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_email TEXT,
    action     TEXT NOT NULL,
    entity     TEXT,
    entity_id  INTEGER,
    detail     TEXT
);
CREATE INDEX IF NOT EXISTS audit_entity_idx ON audit(entity, entity_id);
CREATE INDEX IF NOT EXISTS audit_at_idx ON audit(at DESC);
"""


def db_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return url


def connect():
    return psycopg.connect(db_url(), row_factory=dict_row, autocommit=True)


def get_conn():
    """FastAPI dependency: one connection per request (suits Vercel serverless + Neon pooler)."""
    with connect() as conn:
        yield conn


def init_db(admin_email: str | None, admin_password: str | None, hash_fn):
    with connect() as conn:
        conn.execute(SCHEMA)
        count = conn.execute("SELECT count(*) AS n FROM users").fetchone()["n"]
        if count == 0 and admin_email and admin_password:
            conn.execute(
                "INSERT INTO users (email, name, pw_hash, role) VALUES (%s, %s, %s, 'admin')",
                (admin_email.lower().strip(), "Administrator", hash_fn(admin_password)),
            )


def audit(conn, user_email, action, entity=None, entity_id=None, detail=None):
    conn.execute(
        "INSERT INTO audit (user_email, action, entity, entity_id, detail) VALUES (%s,%s,%s,%s,%s)",
        (user_email, action, entity, entity_id, detail),
    )


def next_asset_code(conn, prefix: str) -> str:
    row = conn.execute(
        """INSERT INTO code_counters (prefix, last) VALUES (%s, 1)
           ON CONFLICT (prefix) DO UPDATE SET last = code_counters.last + 1
           RETURNING last""",
        (prefix,),
    ).fetchone()
    return f"{prefix}-IT-{row['last']:04d}"
