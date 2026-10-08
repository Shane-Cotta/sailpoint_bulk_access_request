#!/usr/bin/env python3
"""Install (or update) the Launcher deployment of Bulk Access Request.

    python launcher/install.py --config config/<tenant>.json --dry-run   # show what would be sent
    python launcher/install.py --config config/<tenant>.json             # create / update
    python launcher/install.py --config config/<tenant>.json --sync-catalog   # refresh catalog choices only

Creates, by prefixed name and idempotently:
  1. the intake Form ("<prefix> Bulk Access Request Form"), with the Request Center
     catalog (filtered by the config) as its item choices,
  2. the Workflow ("<prefix> Bulk Access Request"), scoped to its own Launcher,
  3. the Launcher ("<prefix> Bulk Access Request") shown in users' Launchpad,
  4. the "<prefix> Bulk Access Request - Launcher Access" access profile. SailPoint only
     shows a Launcher to identities holding its `assignedLaunchers` entitlement, so users
     request this profile in the Request Center (or you grant it with --grant).

Re-run with --sync-catalog whenever the Request Center catalog changes.
"""

from __future__ import annotations

import argparse
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))

from bulkaccess import config as config_mod  # noqa: E402
from bulkaccess import definitions, rules  # noqa: E402
from bulkaccess.tenant import Tenant, TenantError  # noqa: E402

FORMS = "/v2025/form-definitions"
WORKFLOWS = "/v2025/workflows"
LAUNCHERS = "/v2025/launchers"
ACCESS_PROFILES = "/v3/access-profiles"


def launcher_entitlement(tenant: Tenant, launcher_id: str, attempts: int = 10) -> dict:
    """The entitlement SailPoint creates for a Launcher (attribute assignedLaunchers = launcher id)."""
    import time
    q = urllib.parse.quote(f'value eq "{launcher_id}"')
    for _ in range(attempts):   # created asynchronously right after the Launcher
        rows = tenant.call("GET", f"/v2025/entitlements?filters={q}&limit=5") or []
        hit = next((e for e in rows if e.get("attribute") == "assignedLaunchers"), None)
        if hit:
            return hit
        time.sleep(3)
    raise TenantError(404, f"No assignedLaunchers entitlement found for launcher {launcher_id} yet; re-run install later.")


def find_access_profile(tenant: Tenant, name: str) -> dict | None:
    q = urllib.parse.quote(f'name eq "{name}"')
    return next((a for a in tenant.call("GET", f"{ACCESS_PROFILES}?filters={q}&limit=5") or [] if a.get("name") == name), None)


def requestable_objects(tenant: Tenant, cfg: config_mod.Config) -> list[dict]:
    """The Request Center catalog, paged, limited to the configured types."""
    out, offset = [], 0
    # Repeat the parameter: a comma list that includes ENTITLEMENT is rejected with a 400.
    types = "&".join(f"types={t}" for t in cfg.catalog_types)
    while True:
        page = tenant.call("GET", f"/v3/requestable-objects?{types}&limit=250&offset={offset}")
        out.extend(page or [])
        if not page or len(page) < 250:
            return out
        offset += 250


def find_form(tenant: Tenant, name: str) -> dict | None:
    q = urllib.parse.quote(f'name eq "{name}"')
    res = tenant.call("GET", f"{FORMS}?filters={q}&limit=10") or {}
    return next((f for f in res.get("results") or [] if f.get("name") == name), None)


def form_element(form: dict, key: str) -> dict | None:
    """The form element with technical key `key` (searching inside sections)."""
    def walk(elements):
        for el in elements or []:
            if el.get("key") == key:
                return el
            found = walk((el.get("config") or {}).get("formElements"))
            if found:
                return found
        return None
    return walk(form.get("formElements"))


def catalog_items(form: dict) -> list[dict]:
    """The access objects the form offers (the values of its STATIC `items` options)."""
    el = form_element(form, definitions.F_ITEMS) or {}
    return [o["value"] for o in (((el.get("config") or {}).get("dataSource") or {}).get("config") or {}).get("options") or []]


def find_workflow(tenant: Tenant, name: str) -> dict | None:
    return next((w for w in tenant.call("GET", f"{WORKFLOWS}?limit=250") or [] if w.get("name") == name), None)


def find_launcher(tenant: Tenant, name: str) -> dict | None:
    res = tenant.call("GET", f"{LAUNCHERS}?limit=100") or {}
    items = res.get("items") if isinstance(res, dict) else res
    return next((l for l in items or [] if l.get("name") == name), None)


def put_workflow(tenant: Tenant, wid: str, body: dict, enable: bool) -> None:
    current = tenant.call("GET", f"{WORKFLOWS}/{wid}")
    if current.get("enabled"):
        # SailPoint refuses some changes to enabled workflows; disable, update, re-enable.
        tenant.call("PATCH", f"{WORKFLOWS}/{wid}", [{"op": "replace", "path": "/enabled", "value": False}],
                    content_type="application/json-patch+json")
    tenant.call("PUT", f"{WORKFLOWS}/{wid}", {**body, "enabled": False})
    if enable:
        tenant.call("PATCH", f"{WORKFLOWS}/{wid}", [{"op": "replace", "path": "/enabled", "value": True}],
                    content_type="application/json-patch+json")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True, help="config/<tenant>.json")
    ap.add_argument("--dry-run", action="store_true", help="print the JSON bodies; change nothing")
    ap.add_argument("--sync-catalog", action="store_true", help="only refresh the form's catalog choices")
    ap.add_argument("--grant", default="", help="comma-separated identity IDs (or 'me') to give Launcher access now")
    a = ap.parse_args(argv)

    cfg = config_mod.load(a.config)
    tenant = Tenant.from_env(cfg.env_file)
    me = tenant.me()
    owner_id = cfg.owner_id or me["id"]
    print(f"Tenant {tenant.tenant_name} · prefix {cfg.prefix!r} · mode {cfg.mode} · owner {me.get('name')}")
    units = definitions.launcher_duration_units(cfg)
    print("Temporary access on the Launcher: "
          + (f"durations in {', '.join(u.lower() for u in units)}"
             + (f", at most {cfg.temporary_max_days} days" if cfg.temporary_max_days else "") if units else "off"))

    options = rules.catalog_options(cfg, requestable_objects(tenant, cfg))
    print(f"Catalog: {len(options)} requestable item(s) offered"
          + (f" (names starting with {cfg.catalog_name_starts_with!r})" if cfg.catalog_name_starts_with else ""))
    if not options:
        print("WARNING: no catalog items match the config; the form would offer nothing to request.")

    form_body = definitions.bulk_form(cfg, owner_id, options)
    form = find_form(tenant, cfg.form_name)

    if a.dry_run:
        print(f"\n== Form ({'update' if form else 'create'}) ==\n{definitions.pretty(form_body)}")
        wf_body = definitions.bulk_workflow(cfg, variant="launcher", owner_id=owner_id, owner_name=me.get("name"),
                                            form_id=(form or {}).get("id", "<form-id>"), workflow_id="<workflow-id>")
        print(f"\n== Workflow ==\n{definitions.pretty(wf_body)}")
        print(f"\n== Launcher ==\n{definitions.pretty(definitions.bulk_launcher(cfg, '<workflow-id>'))}")
        print("\nDry run: nothing was changed.")
        return 0

    # 1. Form
    if form:
        tenant.call("PATCH", f"{FORMS}/{form['id']}", [
            {"op": "replace", "path": "/formElements", "value": form_body["formElements"]},
            {"op": "replace", "path": "/formConditions", "value": form_body["formConditions"]},
            {"op": "replace", "path": "/description", "value": form_body["description"]},
        ], content_type="application/json-patch+json")
        print(f"Form updated:     {form['id']}  {cfg.form_name}")
    else:
        form = tenant.call("POST", FORMS, form_body)
        print(f"Form created:     {form['id']}  {cfg.form_name}")
    if a.sync_catalog:
        print("Catalog synced; workflow and launcher left as they are.")
        return 0

    # 2. Workflow (needs its own id for the trigger filter, so create then update)
    wf = find_workflow(tenant, cfg.launcher_workflow_name)
    if not wf:
        draft = definitions.bulk_workflow(cfg, variant="launcher", owner_id=owner_id, owner_name=me.get("name"),
                                          form_id=form["id"])
        wf = tenant.call("POST", WORKFLOWS, draft)
        print(f"Workflow created: {wf['id']}  {cfg.launcher_workflow_name}")
    body = definitions.bulk_workflow(cfg, variant="launcher", owner_id=owner_id, owner_name=me.get("name"),
                                     form_id=form["id"], workflow_id=wf["id"])
    put_workflow(tenant, wf["id"], {k: body[k] for k in ("name", "description", "owner", "definition", "trigger")},
                 enable=True)
    print(f"Workflow ready:   {wf['id']}  (enabled, trigger scoped to this workflow, mode {cfg.mode})")

    # 3. Launcher
    launcher_body = definitions.bulk_launcher(cfg, wf["id"])
    launcher = find_launcher(tenant, cfg.launcher_name)
    if launcher:
        launcher = tenant.call("PUT", f"{LAUNCHERS}/{launcher['id']}", launcher_body)
        print(f"Launcher updated: {launcher['id']}  {cfg.launcher_name}")
    else:
        launcher = tenant.call("POST", LAUNCHERS, launcher_body)
        print(f"Launcher created: {launcher['id']}  {cfg.launcher_name}")

    # Disabling the workflow during an update switches its Launcher off a moment later,
    # asynchronously; keep re-enabling until it stays on.
    import time
    for _ in range(10):
        time.sleep(3)
        current = tenant.call("GET", f"{LAUNCHERS}/{launcher['id']}")
        if not current.get("disabled"):
            break
        tenant.call("PUT", f"{LAUNCHERS}/{launcher['id']}", launcher_body)
    else:
        print("WARNING: the Launcher is still disabled; enable it in Admin > Launchers or re-run install.")

    # 4. Who can use the Launcher
    ent = launcher_entitlement(tenant, launcher["id"])
    ap_body = definitions.launcher_access_profile(cfg, owner_id, ent)
    access = find_access_profile(tenant, ap_body["name"])
    if access:
        tenant.call("PATCH", f"{ACCESS_PROFILES}/{access['id']}", [
            {"op": "replace", "path": "/entitlements", "value": ap_body["entitlements"]},
            {"op": "replace", "path": "/accessRequestConfig", "value": ap_body["accessRequestConfig"]},
        ], content_type="application/json-patch+json")
        print(f"Access profile updated: {access['id']}  {ap_body['name']}")
    else:
        access = tenant.call("POST", ACCESS_PROFILES, ap_body)
        print(f"Access profile created: {access['id']}  {ap_body['name']}")

    grantees = [me["id"] if g.strip() == "me" else g.strip() for g in a.grant.split(",") if g.strip()]
    if grantees:
        import time
        body = {"requestedFor": grantees, "requestType": "GRANT_ACCESS",
                "requestedItems": [{"type": "ACCESS_PROFILE", "id": access["id"],
                                    "comment": f"Granted by bulk-access-request installer ({cfg.prefix})"}]}
        for attempt in range(10):   # a brand-new access profile takes a little while to become requestable
            try:
                tenant.call("POST", "/v3/access-requests", body)
                break
            except TenantError as exc:
                if exc.status != 400 or "not found" not in str(exc) or attempt == 9:
                    raise
                time.sleep(6)
        print(f"Launcher access requested for {len(grantees)} identit{'y' if len(grantees) == 1 else 'ies'} "
              "(it appears in their Launchpad once provisioned, usually within a minute).")

    print(f"\nDone. Users start it from the Launchpad: '{cfg.launcher_name}'."
          + ("" if cfg.live else "  (dry-run: approvals run, nothing is requested)"))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (config_mod.ConfigError, TenantError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
