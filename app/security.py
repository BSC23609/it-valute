"""Login password hashing, session cookies and vault encryption."""
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
def _fernet() -> Fernet:
    key = os.environ.get("VAULT_KEY")
    if not key:
        raise RuntimeError("VAULT_KEY is not set")
    return Fernet(key.encode())


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
