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
def headers(ot: ObjectType) -> dict[str, str] | None:
    """The type's extra request headers ("Key:Value" lines, as the workflow sends them) as a dict."""
    if not ot.headers:
        return None
    return dict(line.split(":", 1) for line in ot.headers.splitlines() if ":" in line)


def _list_all(tenant: Tenant, ot: ObjectType, query: str = "") -> list[dict[str, Any]]:
    rows, offset = [], 0
    while True:
        page = tenant.call("GET", f"{ot.path}?{query}limit=250&offset={offset}", headers=headers(ot)) or []
        rows.extend(page)
        if len(page) < 250:
            return rows
        offset += 250


def owned(tenant: Tenant, ot: ObjectType, identity_id: str, limit: int = 250) -> list[dict[str, Any]]:
    """The objects of type `ot` whose primary owner is `identity_id`: through the API's owner.id filter where the
    list has one (first `limit`), else every object listed and filtered here."""
    if not ot.server_filter:
        return [o for o in _list_all(tenant, ot) if (o.get("owner") or {}).get("id") == identity_id]
    f = q(f'owner.id eq "{identity_id}"')
    return tenant.call("GET", f"{ot.path}?filters={f}&limit={limit}", headers=headers(ot)) or []


def owned_all(tenant: Tenant, identity_id: str, keys: tuple[str, ...] = tuple(OBJECT_TYPES)) -> dict[str, list[dict]]:
    return {k: owned(tenant, OBJECT_TYPES[k], identity_id) for k in keys}


def count(tenant: Tenant, ot: ObjectType) -> int:
    """How many objects of type `ot` exist (pages through the list)."""
    return len(_list_all(tenant, ot))


def owner_of(tenant: Tenant, ot: ObjectType, object_id: str) -> str | None:
    obj = tenant.call("GET", f"{ot.item_path}/{object_id}", headers=headers(ot)) or {}
    return (obj.get("owner") or {}).get("id")


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
TEST_SUFFIX = {"sources": "Source", "entitlements": "Entitlement", "accessProfiles": "Access Profile",
               "roles": "Role", "governanceGroups": "Governance Group", "identityProfiles": "Identity Profile",
               "apps": "App"}
DELETE_ORDER = ("apps", "identityProfiles", "governanceGroups", "roles", "accessProfiles", "sources")
ABOUT = "Throwaway object for ownership.py test; safe to delete."
# A flat-file source needs its entitlement columns before it can load an entitlement CSV.
GROUP_COLUMNS = ["id", "name", "displayName", "created", "description", "modified", "entitlements", "groups",
                 "permissions"]


def _set_owner(tenant: Tenant, ot: ObjectType, object_id: str, owner_id: str) -> None:
    tenant.call("PATCH", f"{ot.item_path}/{object_id}",
                [{"op": "replace", "path": "/owner", "value": {"type": "IDENTITY", "id": owner_id}}],
                content_type="application/json-patch+json", headers=headers(ot))


def _wait_for_entitlement(tenant: Tenant, source_id: str, name: str, timeout: float = 300) -> dict[str, Any]:
    # The load also turns the CSV's header row into an entitlement (named "displayName"), so pick ours by name.
    f = q(f'source.id eq "{source_id}"')
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rows = tenant.call("GET", f"/v2025/entitlements?filters={f}&limit=10") or []
        hit = next((r for r in rows if r.get("name") == name), None)
        if hit:
            return hit
        time.sleep(5)
    raise TenantError(408, f"the test entitlement didn't appear within {timeout:.0f}s (entitlement aggregation)")


def create_test_objects(tenant: Tenant, prefix: str, owner_id: str, log=print) -> dict[str, dict[str, Any]]:
    """One object of every supported type, all owned by `owner_id`, none requestable or enabled: a flat-file source,
    an entitlement loaded on it, an access profile with that entitlement, a role with that profile, a governance
    group, an identity profile on the source and an app on it. Returned by object type."""
    owner = {"type": "IDENTITY", "id": owner_id}
    made: dict[str, dict[str, Any]] = {}
    name = lambda key: f"{prefix} {TEST_SUFFIX[key]}"  # noqa: E731
    try:
        src = tenant.call("POST", "/v3/sources", {
            "name": name("sources"), "description": ABOUT, "owner": owner, "connector": "delimited-file",
            "connectorAttributes": {"group.columnNames": GROUP_COLUMNS, "group.indexColumn": "id",
                                    "group.delimiter": ","}})
        made["sources"] = src
        ref = {"type": "SOURCE", "id": src["id"], "name": src["name"]}
        log("  loading the test entitlement (an entitlement aggregation; usually under a minute)…")
        csv = (",".join(GROUP_COLUMNS) + "\n" + f"ownership-test,ownership-test,{name('entitlements')},,{ABOUT},,,,\n")
        tenant.upload(f"/v2025/sources/{src['id']}/load-entitlements", "file", "entitlements.csv", csv.encode())
        ent = _wait_for_entitlement(tenant, src["id"], name("entitlements"))
        _set_owner(tenant, OBJECT_TYPES["entitlements"], ent["id"], owner_id)
        made["entitlements"] = ent
        made["accessProfiles"] = tenant.call("POST", "/v3/access-profiles", {
            "name": name("accessProfiles"), "description": ABOUT, "owner": owner, "source": ref,
            "entitlements": [{"type": "ENTITLEMENT", "id": ent["id"], "name": ent["name"]}],
            "enabled": False, "requestable": False})
        ap = made["accessProfiles"]
        made["roles"] = tenant.call("POST", "/v3/roles", {
            "name": name("roles"), "description": ABOUT, "owner": owner,
            "accessProfiles": [{"type": "ACCESS_PROFILE", "id": ap["id"], "name": ap["name"]}],
            "enabled": False, "requestable": False})
        made["governanceGroups"] = tenant.call("POST", "/v2025/workgroups", {
            "name": name("governanceGroups"), "description": ABOUT, "owner": owner})
        made["identityProfiles"] = tenant.call("POST", "/v2025/identity-profiles", {
            "name": name("identityProfiles"), "description": ABOUT, "owner": owner, "authoritativeSource": ref,
            "identityAttributeConfig": {"enabled": True, "attributeTransforms": []}})
        apps = OBJECT_TYPES["apps"]
        app = tenant.call("POST", apps.item_path, {"name": name("apps"), "description": ABOUT, "accountSource": ref,
                                                   "matchAllAccounts": False}, headers=headers(apps))
        made["apps"] = app
        _set_owner(tenant, apps, app["id"], owner_id)      # a new app is owned by its creator, whatever is sent
    except TenantError:
        delete_test_objects(tenant, made, prefix)
        raise
    return made


def find_test_objects(tenant: Tenant, prefix: str) -> dict[str, dict[str, Any]]:
    """Leftovers from an earlier `test --keep` or an interrupted run, by their exact names."""
    found = {}
    for key in DELETE_ORDER:
        name, ot = f"{prefix} {TEST_SUFFIX[key]}", OBJECT_TYPES[key]
        try:
            by_name = q(f'name eq "{name}"')
            rows = _list_all(tenant, ot, f"filters={by_name}&")
        except TenantError as exc:     # a list that can't filter on name: these lists are short
            if exc.status != 400:
                raise
            rows = _list_all(tenant, ot)
        hit = next((r for r in rows if r.get("name") == name), None)
        if hit:
            found[key] = hit
    return found


def delete_test_objects(tenant: Tenant, made: dict[str, dict[str, Any]], prefix: str) -> list[str]:
    """Delete what `create_test_objects` made, dependents first; the entitlement goes with its source. Refuses
    anything whose name doesn't start with the test prefix. Returns problems (empty when all went)."""
    problems = []
    for key in DELETE_ORDER:
        obj = made.get(key)
        if not obj:
            continue
        if not str(obj.get("name", "")).startswith(prefix):
            problems.append(f"refused to delete {obj.get('name')!r}: not a test object")
            continue
        ot = OBJECT_TYPES[key]
        for attempt in range(10):    # dependents' deletes settle asynchronously
            try:
                tenant.call("DELETE", f"{ot.item_path}/{obj['id']}", headers=headers(ot))
                break
            except TenantError as exc:
                if exc.status == 404:
                    break
                if attempt == 9:
                    problems.append(f"could not delete {obj['name']!r}: {exc}")
                time.sleep(4)
    return problems
