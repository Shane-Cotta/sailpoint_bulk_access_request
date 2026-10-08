"""Minimal Identity Security Cloud API client (standard library only).

Deliberately dependency-free so the installers run anywhere Python 3.10+ runs.
Authenticates with a Personal Access Token (client-credentials grant) read from
the environment or a .env file, follows HTTPS_PROXY / NO_PROXY, and sends a
custom User-Agent (Cloudflare in front of some tenants rejects Python's default).
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

USER_AGENT = "bulk-access-request/1.0"
REQUIRED_ENV = ("SAIL_BASE_URL", "SAIL_CLIENT_ID", "SAIL_CLIENT_SECRET")


class TenantError(RuntimeError):
    """An API call failed; carries the HTTP status and SailPoint's message."""

    def __init__(self, status: int, message: str, body: Any = None):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.body = body


def load_env_file(path: str | os.PathLike[str] | None) -> None:
    """Read KEY=VALUE lines into os.environ without overriding real env vars."""
    if not path:
        return
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _messages(body: Any) -> str:
    if isinstance(body, dict):
        msgs = [m.get("text") for m in body.get("messages") or [] if isinstance(m, dict)]
        if msgs:
            return "; ".join(dict.fromkeys(m for m in msgs if m))
        if body.get("message"):
            return str(body["message"]) + (f" {body.get('details')}" if body.get("details") else "")
    return str(body)[:500]


class Tenant:
    def __init__(self, base_url: str, client_id: str, client_secret: str):
        self.base_url = base_url.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._token: str | None = None
        self._token_expires = 0.0

    @classmethod
    def from_env(cls, env_file: str | None = None) -> "Tenant":
        load_env_file(env_file)
        missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
        if missing:
            raise TenantError(0, "Missing credentials: " + ", ".join(missing)
                              + ". Set them in the environment or in the config's envFile.")
        return cls(os.environ["SAIL_BASE_URL"], os.environ["SAIL_CLIENT_ID"], os.environ["SAIL_CLIENT_SECRET"])

    @property
    def tenant_name(self) -> str:
        return urllib.parse.urlsplit(self.base_url).hostname.split(".", 1)[0]

    def _open(self, req: urllib.request.Request, timeout: float = 60):
        return urllib.request.urlopen(req, timeout=timeout)  # honours HTTPS_PROXY / NO_PROXY

    def token(self) -> str:
        if self._token and time.monotonic() < self._token_expires:
            return self._token
        data = urllib.parse.urlencode({
            "grant_type": "client_credentials",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
        }).encode()
        req = urllib.request.Request(f"{self.base_url}/oauth/token", data=data, method="POST",
                                     headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        try:
            with self._open(req) as resp:
                body = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            raise TenantError(exc.code, "could not get an access token; check the PAT and SAIL_BASE_URL") from exc
        self._token = body["access_token"]
        self._token_expires = time.monotonic() + min(int(body.get("expires_in", 3600)), 3600) - 60
        return self._token

    def call(self, method: str, path: str, body: Any = None, *,
             content_type: str = "application/json", ok: tuple[int, ...] = (200, 201, 202, 204)) -> Any:
        """Call `path` (e.g. "/v2025/workflows") and return parsed JSON (or None)."""
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(f"{self.base_url}{path}", data=data, method=method, headers={
            "Authorization": f"Bearer {self.token()}",
            "Content-Type": content_type,
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        })
        try:
            with self._open(req) as resp:
                raw = resp.read()
                status = resp.status
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            status = exc.code
        try:
            parsed = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            parsed = raw.decode(errors="replace")
        if status not in ok:
            raise TenantError(status, _messages(parsed), parsed)
        return parsed

    # ── small helpers used by the installers ─────────────────────────────────
    def me(self) -> dict[str, Any]:
        """The identity the PAT belongs to (owner of everything we create)."""
        claims = json.loads(_b64url(self.token().split(".")[1]))
        identity_id = claims.get("identity_id")
        if not identity_id:
            return {}
        return self.call("GET", f"/v2025/identities/{identity_id}")   # there is no v3 identities API

    def find_by_name(self, list_path: str, name: str, *, key: str | None = None) -> dict[str, Any] | None:
        """First object at `list_path` whose name equals `name` (lists may be wrapped)."""
        sep = "&" if "?" in list_path else "?"
        items = self.call("GET", f"{list_path}{sep}limit=250")
        if isinstance(items, dict):
            items = items.get(key or "results") or items.get("items") or []
        for item in items or []:
            if item.get("name") == name:
                return item
        return None


def _b64url(segment: str) -> str:
    import base64
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)).decode()
