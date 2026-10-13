"""The Request Center catalog as both installers see it (tenant reads; the rules are in rules.py).

The Launcher installer offers it in the intake form; both installers work out which items SailPoint grants without
any approval, so the form and the plugin can say so (there is no approval of the bulk request itself).
"""

from __future__ import annotations

import urllib.parse
from typing import Any

from . import api, rules
from .config import Config
from .tenant import Tenant

# How many IDs go into one `id in (…)` filter (well within URL limits).
ID_BATCH = 50


def paged(tenant: Tenant, path: str) -> list[dict[str, Any]]:
    """Every row of a list endpoint (`path` already carries its query string), 250 at a time."""
    out, offset = [], 0
    while True:
        page = tenant.call("GET", f"{path}&limit=250&offset={offset}")
        out.extend(page or [])
        if not page or len(page) < 250:
            return out
        offset += 250


def requestable_objects(tenant: Tenant, cfg: Config) -> list[dict[str, Any]]:
    """The Request Center catalog, paged, limited to the configured types.

    Access profiles and roles come from /requestable-objects, which can't list entitlements (CONTRACTS §8);
    requestable entitlements come from /entitlements, tagged ENTITLEMENT.
    """
    out = []
    types = rules.requestable_object_types(cfg)
    if types:   # without `types` the API would return every type
        out += paged(tenant, f"{api.REQUESTABLE_OBJECTS}?" + "&".join(f"types={t}" for t in types))
    flt = rules.entitlement_filter(cfg)
    if flt:
        out += [{**e, "type": "ENTITLEMENT"}
                for e in paged(tenant, f"{api.ENTITLEMENTS}?filters={urllib.parse.quote(flt)}&sorters=name")]
    return out


def _schemes(tenant: Tenant, path: str, ids: list[str], kind: str) -> list[dict[str, Any]]:
    """{id, type, approvalSchemes} for access profiles or roles, read in batches by ID."""
    out = []
    for i in range(0, len(ids), ID_BATCH):
        batch = ids[i:i + ID_BATCH]
        flt = urllib.parse.quote("id in (" + ",".join(rules.filter_quote(x) for x in batch) + ")")
        for row in paged(tenant, f"{path}?filters={flt}"):
            out.append({"id": row["id"], "type": kind,
                        "approvalSchemes": (row.get("accessRequestConfig") or {}).get("approvalSchemes") or []})
    return out


def no_approval_ids(tenant: Tenant, objects: list[dict[str, Any]]) -> set[str]:
    """Which of the catalog `objects` SailPoint grants without any approval (rules.no_approval_ids). Reads each access
    profile's and role's approval schemes, each entitlement's own request config, and the tenant default for
    entitlements (one call per entitlement: run at install time, not per request)."""
    by_type: dict[str, list[str]] = {}
    for obj in objects:
        by_type.setdefault(obj.get("type", ""), []).append(obj["id"])
    rows = (_schemes(tenant, api.ACCESS_PROFILES, by_type.get("ACCESS_PROFILE", []), "ACCESS_PROFILE")
            + _schemes(tenant, api.ROLES, by_type.get("ROLE", []), "ROLE"))
    for eid in by_type.get("ENTITLEMENT", []):
        config = tenant.call("GET", f"{api.ENTITLEMENTS}/{eid}/entitlement-request-config") or {}
        rows.append({"id": eid, "type": "ENTITLEMENT",
                     "approvalSchemes": (config.get("accessRequestConfig") or {}).get("approvalSchemes") or []})
    default = None
    if by_type.get("ENTITLEMENT"):
        default = ((tenant.call("GET", api.ACCESS_REQUEST_CONFIG) or {}).get("entitlementRequestConfig") or {}) \
            .get("grantRequestApprovalSchemes")
    return rules.no_approval_ids(rows, default)
