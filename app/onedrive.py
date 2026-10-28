"""Copy uploaded invoice files to OneDrive (Microsoft Graph, app-only).

Set these in Vercel → Settings → Environment Variables:
  MS_TENANT_ID, MS_CLIENT_ID, MS_CLIENT_SECRET  – an Entra app with the *Application* permission
                                                 Files.ReadWrite.All (admin consent granted)
  ONEDRIVE_USER    – whose OneDrive receives the files, e.g. ai@bharatsteels.in
  ONEDRIVE_FOLDER  – optional, default "IT Asset Invoices" (created automatically)
"""
import json
import os
import re
import time
import urllib.parse
import urllib.request

_token = {"value": None, "exp": 0}


def _env(name, default=""):
    return os.environ.get(name, default).strip().strip('"').strip("'")


def configured() -> bool:
    return all(_env(k) for k in ("MS_TENANT_ID", "MS_CLIENT_ID", "MS_CLIENT_SECRET", "ONEDRIVE_USER"))


def folder() -> str:
    return _env("ONEDRIVE_FOLDER", "IT Asset Invoices").strip("/") or "IT Asset Invoices"


def _get_token():
    if _token["value"] and _token["exp"] > time.time() + 60:
        return _token["value"]
    body = urllib.parse.urlencode({
        "client_id": _env("MS_CLIENT_ID"), "client_secret": _env("MS_CLIENT_SECRET"),
        "scope": "https://graph.microsoft.com/.default", "grant_type": "client_credentials",
    }).encode()
    req = urllib.request.Request(
        f"https://login.microsoftonline.com/{_env('MS_TENANT_ID')}/oauth2/v2.0/token", data=body, method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.loads(r.read())
    _token.update(value=data["access_token"], exp=time.time() + int(data.get("expires_in", 3600)))
    return _token["value"]


def safe_name(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|#%]+', "-", name or "").strip(" .")
    return name[:120] or "file"


def upload(subfolder: str, filename: str, data: bytes, content_type: str | None = None) -> str:
    """Upload (≤ 4 MB) to <ONEDRIVE_FOLDER>/<subfolder>/<filename>; missing folders are created.
    Returns the file's OneDrive web link. Raises on failure."""
    path = "/".join(safe_name(p) for p in (folder(), subfolder, filename) if p)
    url = (f"https://graph.microsoft.com/v1.0/users/{urllib.parse.quote(_env('ONEDRIVE_USER'))}"
           f"/drive/root:/{urllib.parse.quote(path)}:/content?@microsoft.graph.conflictBehavior=rename")
    req = urllib.request.Request(url, data=data, method="PUT", headers={
        "Authorization": f"Bearer {_get_token()}",
        "Content-Type": content_type or "application/octet-stream"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read()).get("webUrl", "")


def explain(exc: Exception) -> str:
    """Short, readable reason for a failed upload."""
    detail = ""
    if hasattr(exc, "read"):
        try:
            err = json.loads(exc.read() or b"{}")
            detail = err.get("error_description") or (err.get("error") or {}).get("message", "")
        except Exception:
            pass
    code = getattr(exc, "code", "")
    return (f"{code} {detail}".strip() or str(exc))[:300]
