"""Tenant-side helpers for the installer: resolve identities, find what someone owns, create or update the
workflow, run it through the test endpoint, and the throwaway objects `ownership.py test` uses."""

from __future__ import annotations

import time
import urllib.parse
from typing import Any

from .tenant import Tenant, TenantError
from .workflow import OAUTH_PARAMETER_TYPE, OBJECT_TYPES, ObjectType

WORKFLOWS = "/v2025/workflows"
EXECUTIONS = "/v2025/workflow-executions"
IDENTITIES = "/v2025/identities"          # there is no v3 identities API
PARAMETERS = "/v2025/parameter-storage/parameters"
FINISHED = ("Completed", "Failed", "Canceled")


def q(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def api_url(tenant: Tenant) -> str:
    """The API base the workflow's HTTP steps call: the installer's own SAIL_BASE_URL, whatever the domain."""
    return tenant.base_url


# ── identities ──────────────────────────────────────────────────────────────
def resolve_identity(tenant: Tenant, ref: str) -> dict[str, Any]:
    """An identity by ID, username (alias / name) or email address; raises TenantError(404) if none or ambiguous."""
    if "@" not in ref:
        try:
            return tenant.call("GET", f"{IDENTITIES}/{q(ref)}")
        except TenantError as exc:
            if exc.status not in (400, 404):
                raise
    filters = (f'emailAddress eq "{ref}"' if "@" in ref else f'alias eq "{ref}" or name eq "{ref}"')
    rows = tenant.call("GET", f"{IDENTITIES}?filters={q(filters)}&limit=3") or []
    if len(rows) == 1:
        return rows[0]
    raise TenantError(404, f"{ref!r} matches {len(rows)} identities; use the identity ID")


def lifecycle_state(identity: dict[str, Any]) -> str | None:
    """The technical lifecycle state name from a /v2025/identities row (None when the identity has none)."""
    return ((identity.get("lifecycleState") or {}).get("stateName")
            or (identity.get("attributes") or {}).get("cloudLifecycleState"))


# ── the HTTP steps' credential ───────────────────────────────────────────────
def find_parameter(tenant: Tenant, ref: str) -> dict[str, Any]:
    """The Parameter Storage parameter named (or with the ID) `ref`; it must be an OAuth 2.0 Client Credentials
    parameter. Secrets never leave Parameter Storage: the API returns only its public fields."""
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = tenant.call("GET", f"{PARAMETERS}?limit=250&offset={offset}") or []
        rows.extend(page)
        if len(page) < 250:
            break
        offset += 250
    hits = [p for p in rows if ref in (p.get("id"), p.get("name"))]
    if len(hits) != 1:
        raise TenantError(404, f"Parameter Storage has {len(hits)} parameters named {ref!r}. Create one "
                               "(Admin > Global > Parameter Storage > Create Parameter, category Authentication, "
                               "type OAuth 2.0 - Client Credentials Grant) or set http.parameter to its ID.")
    p = hits[0]
    if p.get("type") != OAUTH_PARAMETER_TYPE:
        raise TenantError(400, f"parameter {p.get('name')!r} has type {p.get('type')}, not OAuth 2.0 Client "
                               f"Credentials ({OAUTH_PARAMETER_TYPE})")
    return p


# ── ownership ───────────────────────────────────────────────────────────────
def owned(tenant: Tenant, ot: ObjectType, identity_id: str, limit: int = 250) -> list[dict[str, Any]]:
    """The objects of type `ot` whose primary owner is `identity_id` (first `limit`, by the API's own filter)."""
    f = q(f'owner.id eq "{identity_id}"')
    return tenant.call("GET", f"{ot.path}?filters={f}&limit={limit}") or []


def owned_all(tenant: Tenant, identity_id: str, keys: tuple[str, ...] = tuple(OBJECT_TYPES)) -> dict[str, list[dict]]:
    return {k: owned(tenant, OBJECT_TYPES[k], identity_id) for k in keys}


def owner_of(tenant: Tenant, ot: ObjectType, object_id: str) -> str | None:
    return ((tenant.call("GET", f"{ot.path}/{object_id}") or {}).get("owner") or {}).get("id")


# ── workflow ────────────────────────────────────────────────────────────────
def find_workflow(tenant: Tenant, name: str) -> dict[str, Any] | None:
    return next((w for w in tenant.call("GET", f"{WORKFLOWS}?limit=250") or [] if w.get("name") == name), None)


def set_enabled(tenant: Tenant, wid: str, enabled: bool) -> None:
    tenant.call("PATCH", f"{WORKFLOWS}/{wid}", [{"op": "replace", "path": "/enabled", "value": enabled}],
                content_type="application/json-patch+json")


def put_workflow(tenant: Tenant, wid: str, body: dict[str, Any], enable: bool) -> None:
    """Update in place. ISC refuses some changes to enabled workflows: disable, PUT, then re-enable if asked."""
    if (tenant.call("GET", f"{WORKFLOWS}/{wid}") or {}).get("enabled"):
        set_enabled(tenant, wid, False)
    tenant.call("PUT", f"{WORKFLOWS}/{wid}",
                {**{k: body[k] for k in ("name", "description", "owner", "definition", "trigger")}, "enabled": False})
    if enable:
        set_enabled(tenant, wid, True)


def run_test(tenant: Tenant, wid: str, payload: dict[str, Any], timeout: float = 240) -> dict[str, Any]:
    """Run a disabled workflow with `payload` as the trigger input (needs ORG_ADMIN); returns the finished execution."""
    started = tenant.call("POST", f"{WORKFLOWS}/{wid}/test", {"input": payload})
    exec_id = started["workflowExecutionId"]
    deadline = time.monotonic() + timeout
    while True:
        try:
            ex = tenant.call("GET", f"{EXECUTIONS}/{exec_id}")
        except TenantError as exc:
            if exc.status != 404:        # the execution can take a moment to appear
                raise
            ex = {"status": "Queued"}
        if ex.get("status") in FINISHED:
            return {**ex, "id": exec_id}
        if time.monotonic() > deadline:
            raise TenantError(408, f"execution {exec_id} still {ex.get('status')} after {timeout:.0f}s")
        time.sleep(3)


def history(tenant: Tenant, exec_id: str) -> list[dict[str, Any]]:
    return tenant.call("GET", f"{EXECUTIONS}/{exec_id}/history") or []


def history_summary(events: list[dict[str, Any]]) -> list[str]:
    """One line per step event; failures carry their error (as much as ISC records)."""
    lines = []
    for e in events:
        kind = e.get("type", "")
        if kind in ("ActivityTaskScheduled", "WorkflowExecutionStarted"):
            continue
        name = e.get("displayName") or (e.get("attributes") or {}).get("displayName") or ""
        err = (e.get("attributes") or {}).get("error") or (e.get("attributes") or {}).get("reason")
        lines.append(f"{kind:<28} {name}" + (f"  ERROR: {str(err)[:300]}" if err else ""))
    return lines


# ── throwaway test objects (ownership.py test) ─────────────────────────────
def create_test_objects(tenant: Tenant, prefix: str, owner_id: str) -> dict[str, dict[str, Any]]:
    """A flat-file source, an access profile on it and a role holding that profile, all owned by `owner_id`,
    none requestable or enabled. Returned in creation order; `delete_test_objects` removes them in reverse."""
    owner = {"type": "IDENTITY", "id": owner_id}
    made: dict[str, dict[str, Any]] = {}
    try:
        src = tenant.call("POST", "/v3/sources", {
            "name": f"{prefix} Source", "description": "Throwaway object for ownership.py test; safe to delete.",
            "owner": owner, "connector": "delimited-file"})
        made["sources"] = src
        ap = tenant.call("POST", "/v3/access-profiles", {
            "name": f"{prefix} Access Profile", "description": "Throwaway object for ownership.py test; safe to delete.",
            "owner": owner, "source": {"type": "SOURCE", "id": src["id"], "name": src["name"]},
            "entitlements": [], "enabled": False, "requestable": False})
        made["accessProfiles"] = ap
        role = tenant.call("POST", "/v3/roles", {
            "name": f"{prefix} Role", "description": "Throwaway object for ownership.py test; safe to delete.",
            "owner": owner, "accessProfiles": [{"type": "ACCESS_PROFILE", "id": ap["id"], "name": ap["name"]}],
            "enabled": False, "requestable": False})
        made["roles"] = role
    except TenantError:
        delete_test_objects(tenant, made, prefix)
        raise
    return made


def find_test_objects(tenant: Tenant, prefix: str) -> dict[str, dict[str, Any]]:
    """Leftovers from an earlier `test --keep` or an interrupted run, by their exact names."""
    found = {}
    for key, suffix in (("sources", "Source"), ("accessProfiles", "Access Profile"), ("roles", "Role")):
        name = f"{prefix} {suffix}"
        f = q(f'name eq "{name}"')
        rows = tenant.call("GET", f"{OBJECT_TYPES[key].path}?filters={f}&limit=2") or []
        hit = next((r for r in rows if r.get("name") == name), None)
        if hit:
            found[key] = hit
    return found


def delete_test_objects(tenant: Tenant, made: dict[str, dict[str, Any]], prefix: str) -> list[str]:
    """Delete what `create_test_objects` made (role, then access profile, then source). Refuses anything whose
    name doesn't start with the test prefix. Returns problems (empty when all went)."""
    problems = []
    for key in ("roles", "accessProfiles", "sources"):
        obj = made.get(key)
        if not obj:
            continue
        if not str(obj.get("name", "")).startswith(prefix):
            problems.append(f"refused to delete {obj.get('name')!r}: not a test object")
            continue
        for attempt in range(5):     # an access profile can't go while the role still lists it; deletes settle async
            try:
                tenant.call("DELETE", f"{OBJECT_TYPES[key].path}/{obj['id']}")
                break
            except TenantError as exc:
                if exc.status == 404:
                    break
                if attempt == 4:
                    problems.append(f"could not delete {obj['name']!r}: {exc}")
                time.sleep(3)
    return problems
