"""Who may make bulk requests: the plugin's Submit Access role (tenant reads and writes; the bodies are in definitions.py).

The plugin installer creates the role and keeps it in line with the config; the plugin lets its holders submit
(CONTRACTS §9). With the Launcher deployed the role contains the Launcher Access profile, so one request covers both.
People who already hold the Launcher Access profile are given the role, so nobody loses access when the plugin's
check moves to the role or the Launcher is retired.
"""

from __future__ import annotations

import time
import urllib.parse
from typing import Any, Iterable

from . import api, definitions, rules
from .config import ACCESS_REQUEST_PEOPLE_MAX, Config
from .tenant import Tenant, TenantError


def find_named(tenant: Tenant, path: str, name: str) -> dict[str, Any] | None:
    """The access profile or role (`path`: api.ACCESS_PROFILES or api.ROLES) with exactly this name."""
    flt = urllib.parse.quote(f"name eq {rules.filter_quote(name)}")
    return next((o for o in tenant.call("GET", f"{path}?filters={flt}&limit=5") or [] if o.get("name") == name), None)


def holders(tenant: Tenant, item_id: str) -> list[dict[str, Any]]:
    """The identities that hold an access profile or role, from the search index (`{id, name}`)."""
    body = {"indices": ["identities"], "query": {"query": f'@access(id:"{item_id}")'},
            "queryResultFilter": {"includes": ["id", "name"]}, "sort": ["name"]}
    return [{"id": r["id"], "name": r.get("name")} for r in tenant.call("POST", f"{api.SEARCH}?limit=10000", body) or []]


def request(tenant: Tenant, item: dict[str, Any], identity_ids: Iterable[str], comment: str) -> int:
    """File an access request for `item` (`{id, type}`) for these identities, 10 per request. Returns how many."""
    ids = list(dict.fromkeys(identity_ids))
    for start in range(0, len(ids), ACCESS_REQUEST_PEOPLE_MAX):
        body = {"requestedFor": ids[start:start + ACCESS_REQUEST_PEOPLE_MAX], "requestType": "GRANT_ACCESS",
                "requestedItems": [{"type": item["type"], "id": item["id"], "comment": comment}]}
        for attempt in range(10):   # a brand-new role or access profile takes a little while to become requestable
            try:
                tenant.call("POST", api.ACCESS_REQUESTS, body)
                break
            except TenantError as exc:
                if exc.status != 400 or "not found" not in str(exc) or attempt == 9:
                    raise
                time.sleep(6)
    return len(ids)


def apply_role(tenant: Tenant, cfg: Config, owner_id: str, launcher_access: dict[str, Any] | None,
               dry_run: bool = False) -> tuple[dict[str, Any] | None, str]:
    """Create the Submit Access role, or PATCH only what differs. Returns (role, what happened); the role is None
    on a dry run that would create it."""
    wanted = definitions.submit_role(cfg, owner_id, launcher_access)
    role = find_named(tenant, api.ROLES, wanted["name"])
    if not role:
        if dry_run:
            return None, "would be created"
        return tenant.call("POST", api.ROLES, wanted), "created"
    changes = definitions.role_changes(role, wanted)
    if not changes:
        return role, "unchanged"
    fields = ", ".join(op["path"].lstrip("/") for op in changes)
    if dry_run:
        return role, f"would be updated ({fields})"
    tenant.call("PATCH", f"{api.ROLES}/{role['id']}", changes, content_type="application/json-patch+json")
    return role, f"updated ({fields})"


def to_migrate(tenant: Tenant, role: dict[str, Any] | None, launcher_access: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Holders of the Launcher Access profile who don't hold the role yet."""
    if not launcher_access:
        return []
    have = {h["id"] for h in holders(tenant, role["id"])} if role else set()
    return [h for h in holders(tenant, launcher_access["id"]) if h["id"] not in have]


__all__ = ["apply_role", "find_named", "holders", "request", "to_migrate"]
