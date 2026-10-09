"""Login password hashing, session cookies and vault encryption."""
import base64
import hashlib
import hmac
import os
import secrets

from cryptography.fernet import Fernet, InvalidToken
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

SESSION_COOKIE = "itv_session"
SESSION_MAX_AGE = 12 * 3600  # 12 hours


# ---------- login passwords (scrypt, stdlib) ----------
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_hex, digest_hex = stored.split("$")
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1, dklen=32)
        return hmac.compare_digest(digest.hex(), digest_hex)
    except Exception:
        return False


# ---------- sessions ----------
def _serializer() -> URLSafeTimedSerializer:
    key = os.environ.get("SECRET_KEY")
    if not key:
        raise RuntimeError("SECRET_KEY is not set")
    return URLSafeTimedSerializer(key, salt="itv-session")


def make_session(user) -> str:
    # pw_hash fingerprint: changing a password logs out that user's other sessions
    return _serializer().dumps({"uid": user["id"], "pv": user["pw_hash"][-12:]})


def read_session(token: str | None):
    if not token:
        return None
    try:
        return _serializer().loads(token, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None


# ---------- vault encryption (Fernet = AES-128-CBC + HMAC-SHA256) ----------
def _clean(raw: str) -> str:
    raw = (raw or "").strip().strip("\"'").strip()
    if raw.upper().startswith("VAULT_KEY="):
        raw = raw[10:].strip().strip("\"'").strip()
    return raw


def vault_key_problem() -> str | None:
    """None if VAULT_KEY is usable, otherwise a short reason."""
    raw = _clean(os.environ.get("VAULT_KEY", ""))
    if not raw:
        return "missing"
    try:
        Fernet(raw.encode())
        return None
    except Exception:
        return None if len(raw) >= 32 else "too short"


def _fernet() -> Fernet:
    raw = _clean(os.environ.get("VAULT_KEY", ""))
    if not raw:
        raise RuntimeError("VAULT_KEY is not set")
    try:
        return Fernet(raw.encode())          # a proper Fernet key is used as-is
    except Exception:
        if len(raw) < 32:
            raise RuntimeError("VAULT_KEY is too short")
        # any other long random secret: derive a Fernet key from it
        return Fernet(base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest()))


def encrypt(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    return _fernet().encrypt(value.encode()).decode()


def decrypt(token: str | None) -> str:
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        return "⚠ cannot decrypt — VAULT_KEY changed?"
