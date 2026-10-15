#!/usr/bin/env python3
"""Install (or update) the UI plugin deployment of Bulk Access Request.

    python plugin/install.py --config config/<tenant>.json --dry-run   # print every body; change nothing
    python plugin/install.py --config config/<tenant>.json             # write the runtime config
    python plugin/install.py --config config/<tenant>.json --deploy    # ...then build and upload the plugin

What it does, idempotently:
  1. How the plugin submits depends on the config's `plugin.submit`:
     - "direct" (the default): the plugin files the access requests itself, as the signed-in user. That needs the
       tenant to let users request access for others; if it doesn't, the plugin submits through the Launcher
       instead (when the Launcher deployment is on) and this script says so.
     - "launcher": looks up the Launcher "<prefix> Bulk Access Request" (install the Launcher deployment first).
       The plugin launches it and submits its form as the signed-in user.
     Either way each item is approved the way SailPoint has it set up.
  2. Creates (or updates only what differs) the requestable role "<prefix> Bulk Access Request - Submit Access":
     only its holders may submit (the plugin checks it). With the Launcher deployed it contains the Launcher Access
     profile, which is then no longer requestable on its own. Holders of the Launcher Access profile get the role
     (an access request, approved like any other request for it), and --grant requests it for others.
  3. Works out which catalog items SailPoint grants without any approval (the page labels them).
  4. Writes public/bulk-access.config.json (submit mode, IDs, INC rule, limits, catalog filter, those items) and
     sp-ui-plugin.json (alias and name from the config).
  5. With --deploy: `npm run build`, then `sail ui-plugins create --private` (first
     time only, push-manifest after that) and `sail ui-plugins upload`. The plugin is
     private unless the config's `plugin.public` is true or --public is given.
     --workdir builds another copy of this folder (one that has node_modules); the
     generated files are then written there instead of here.
"""

from __future__ import annotations

import argparse
import dataclasses
import subprocess
import sys
from pathlib import Path

import pluginlib as lib
from pluginlib import TenantError, config_mod, definitions


def submit_role(tenant: lib.Tenant, cfg: lib.Config, owner_id: str, dry_run: bool) -> dict | None:
    """The Submit Access role: created or brought in line with the config, and given to everyone who holds the
    Launcher Access profile (the old way to submit) but not the role yet. None on a dry run that would create it."""
    sa = lib.submit_access
    launcher_access = sa.find_named(tenant, lib.api.ACCESS_PROFILES, cfg.launcher_access_profile_name)
    role, done = sa.apply_role(tenant, cfg, owner_id, launcher_access if cfg.deploy_launcher else None, dry_run)
    print(f"Submit Access role {done}: " + (f"{role['id']}  " if role else "") + cfg.submit_role_name
          + (f" (contains '{launcher_access['name']}')" if launcher_access and cfg.deploy_launcher else ""))
    missing = sa.to_migrate(tenant, role, launcher_access)
    if missing:
        names = ", ".join(h["name"] or h["id"] for h in missing)
        if dry_run:
            print(f"Dry run: would request the role for {len(missing)} holder(s) of "
                  f"'{launcher_access['name']}': {names}")
        else:
            sa.request(tenant, {"type": "ROLE", "id": role["id"]}, [h["id"] for h in missing],
                       f"Bulk access request: moved from '{launcher_access['name']}' ({cfg.prefix})")
            print(f"Requested the role for {len(missing)} holder(s) of '{launcher_access['name']}': {names}"
                  + (" (each goes to their manager for approval: access.approval is MANAGER)"
                     if cfg.access_approval == "MANAGER" else ""))
    return role


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True, help="config/<tenant>.json")
    ap.add_argument("--dry-run", action="store_true", help="print every JSON body; change nothing")
    ap.add_argument("--deploy", action="store_true", help="also build the plugin and upload it with the SailPoint CLI")
    ap.add_argument("--workdir", default=str(lib.PLUGIN_DIR),
                    help="Angular project to build and upload (default: this folder)")
    seen = ap.add_mutually_exclusive_group()
    seen.add_argument("--public", dest="public", action="store_true", default=None,
                      help="with --deploy: make the plugin visible to everyone, not just you "
                           "(default: the config's plugin.public, which defaults to false)")
    seen.add_argument("--private", dest="public", action="store_false", default=None,
                      help="with --deploy: keep the plugin private to you even if plugin.public is true")
    ap.add_argument("--grant", default="", help="comma-separated identity IDs (or 'me') to request the Submit Access "
                                                "role for")
    a = ap.parse_args(argv)

    cfg = config_mod.load(a.config)
    public = cfg.plugin_public if a.public is None else a.public
    tenant = lib.Tenant.from_env(cfg.env_file)
    print(f"Tenant {tenant.tenant_name} · prefix {cfg.prefix!r} · mode {cfg.mode}")

    if cfg.plugin_submits_directly and not lib.may_request_for_others(tenant):
        if not cfg.deploy_launcher:
            raise TenantError(0, "plugin.submit is \"direct\", but this tenant doesn't let users request access for "
                                 "others (Admin > Global > Access Request Settings). Allow it, or install the "
                                 "Launcher deployment and set plugin.submit to \"launcher\".")
        print("note: this tenant doesn't let users request access for others, so the plugin submits through the "
              f"Launcher '{cfg.launcher_name}' instead (plugin.submit \"launcher\").")
        cfg = dataclasses.replace(cfg, plugin_submit="launcher")
    via_launcher = cfg.plugin_submits_via_launcher
    launcher = lib.find_launcher(tenant, cfg.launcher_name) if via_launcher else None
    print("Submit: " + (f"through the Launcher '{cfg.launcher_name}' "
                        + (f"({launcher['id']})" if launcher else "(not installed yet)") if via_launcher
                        else "the plugin files the access requests itself")
          + f", as the signed-in user; only holders of the role '{cfg.submit_role_name}' may submit")
    old = lib.find_workflow(tenant, cfg.old_plugin_workflow_name)
    if old:
        print(f"note: the workflow '{old['name']}' ({old['id']}) is no longer used; "
              "`bulkaccess.py uninstall --only plugin` removes it.")

    objects = lib.catalog.requestable_objects(tenant, cfg)
    # Only the items the page offers (catalog_options drops the tool's own profile and filtered-out items).
    offered = {o["value"]["id"] for o in lib.rules.catalog_options(cfg, objects)}
    no_approval = lib.catalog.no_approval_ids(tenant, objects) & offered
    print(f"Catalog: {len(no_approval)} item(s) have no approval of their own; the page labels them "
          "\"No approval: granted right away\".")
    me = tenant.me()
    role = submit_role(tenant, cfg, cfg.owner_id or me["id"], a.dry_run)
    grantees = [me["id"] if g.strip() == "me" else g.strip() for g in a.grant.split(",") if g.strip()]
    if grantees and role and not a.dry_run:
        n = lib.submit_access.request(tenant, {"type": "ROLE", "id": role["id"]}, grantees,
                                      f"Granted by bulk-access-request installer ({cfg.prefix})")
        print(f"Submit Access requested for {n} identit{'y' if n == 1 else 'ies'} "
              "(the plugin lets them submit once it's granted, usually within a minute).")
    elif grantees:
        print(f"Dry run: would request the Submit Access role for {len(grantees)} identities.")
    runtime = lib.runtime_config(cfg, (launcher or {}).get("id"), (role or {}).get("id"), no_approval)
    workdir = Path(a.workdir).resolve()

    if a.dry_run:
        if via_launcher and not launcher:
            print(f"note: {lib.launcher_missing(cfg)}")
        print(f"\n== {lib.RUNTIME_CONFIG} ==\n{definitions.pretty(runtime)}")
        print(f"\n== {lib.MANIFEST} ==\n{definitions.pretty(lib.manifest(cfg))}")
        if a.deploy:
            print(f"\n== Deploy (in {workdir}) ==\nnpm run build\n"
                  f"sail ui-plugins create|push-manifest {'' if public else '--private'}  (create only if alias {cfg.plugin_alias!r} is new)\n"
                  "sail ui-plugins upload")
        print("\nDry run: nothing was changed.")
        return 0

    if via_launcher and not launcher:
        raise TenantError(404, lib.launcher_missing(cfg))

    # Runtime config + manifest, in the project that gets built
    lib.write_json(workdir / lib.RUNTIME_CONFIG, runtime)
    lib.write_json(workdir / lib.MANIFEST, lib.manifest(cfg))
    print(f"Wrote {workdir / lib.RUNTIME_CONFIG} and {workdir / lib.MANIFEST}")

    # Build and upload
    if a.deploy:
        if not (workdir / "node_modules").exists():
            raise TenantError(0, f"{workdir} has no node_modules. Run `npx -y npm@11 install` there first.")
        print("Building (npm run build)…")
        subprocess.run(["npm", "run", "build"], cwd=workdir, check=True, stdout=subprocess.DEVNULL)
        plugin = lib.find_plugin(cfg, workdir)
        # push-manifest replaces the whole manifest, visibility included, so --private
        # must be repeated on every update or the plugin becomes visible to everyone.
        visibility = [] if public else ["--private"]
        if plugin:
            lib.sail(cfg, ["ui-plugins", "push-manifest", *visibility], workdir)
            print(f"Plugin manifest pushed: {plugin.get('id')}  alias {cfg.plugin_alias}"
                  + ("  (visible to everyone)" if public else "  (private to you)"))
        else:
            print(lib.sail(cfg, ["ui-plugins", "create", *visibility], workdir).stdout.strip())
        print(lib.sail(cfg, ["ui-plugins", "upload"], workdir).stdout.strip())
        plugin = lib.find_plugin(cfg, workdir) or {}
        ui = config_mod.ui_base_url(cfg, tenant.base_url)
        print(f"Plugin deployed: {ui or tenant.base_url.replace('.api.', '.')}/ui/plugin/{plugin.get('id', '<id>')}")

    print("\nDone." + ("" if cfg.live else "  (dry-run mode: the checks run, nothing is requested)"))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (config_mod.ConfigError, TenantError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError as exc:   # npm or sail missing
        print(f"ERROR: {exc}. Install Node.js/npm and the SailPoint CLI (sail 2.7+).", file=sys.stderr)
        sys.exit(1)
