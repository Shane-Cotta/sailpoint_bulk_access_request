#!/usr/bin/env python3
"""Install (or update) the UI plugin deployment of Bulk Access Request.

    python plugin/install.py --config config/<tenant>.json --dry-run   # print every body; change nothing
    python plugin/install.py --config config/<tenant>.json             # runtime config (+ workflow)
    python plugin/install.py --config config/<tenant>.json --deploy    # ...then build and upload the plugin

What it does, idempotently and by prefixed name:
  1. How the plugin submits depends on the config's `plugin.submit`:
     - "launcher" (the default when the Launcher deployment is on): looks up the Launcher
       "<prefix> Bulk Access Request" (install the Launcher deployment first). The plugin
       launches it and submits its form as the signed-in user, so anyone holding the
       Launcher Access profile can submit; no plugin workflow is needed.
     - "test-endpoint": creates or updates the DISABLED workflow "<prefix> Bulk Access
       Request (Plugin)", owned by the PAT's identity (or `owner` from the config). The
       plugin starts it through the workflow test endpoint (ORG_ADMIN only), so it must
       stay disabled.
  2. Writes public/bulk-access.config.json (submit mode, Launcher or workflow ID, INC rule,
     limits, catalog filter) and sp-ui-plugin.json (alias and name from the config).
  3. With --deploy: `npm run build`, then `sail ui-plugins create --private` (first
     time only, push-manifest after that) and `sail ui-plugins upload`. The plugin is
     private unless the config's `plugin.public` is true or --public is given.
     --workdir builds another copy of this folder (one that has node_modules); the
     generated files are then written there instead of here.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import pluginlib as lib
from pluginlib import TenantError, config_mod, definitions


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
    a = ap.parse_args(argv)

    cfg = config_mod.load(a.config)
    public = cfg.plugin_public if a.public is None else a.public
    tenant = lib.Tenant.from_env(cfg.env_file)
    me = lib.whoami(tenant)
    owner_id = cfg.owner_id or me["id"]
    owner_name = me.get("name") if owner_id == me["id"] else None
    print(f"Tenant {tenant.tenant_name} · prefix {cfg.prefix!r} · mode {cfg.mode} · owner {owner_name or owner_id}")
    via_launcher = cfg.plugin_submits_via_launcher
    if not cfg.owner_id and not via_launcher:   # only the plugin workflow has an owner
        print(f"note: {config_mod.OWNER_NOTE}")

    body = None if via_launcher else lib.plugin_workflow(cfg, owner_id, owner_name)
    existing = lib.find_workflow(tenant, cfg.plugin_workflow_name)
    launcher = lib.find_launcher(tenant, cfg.launcher_name) if via_launcher else None
    workdir = Path(a.workdir).resolve()
    if via_launcher:
        print(f"Submit: through the Launcher '{cfg.launcher_name}' "
              + (f"({launcher['id']})" if launcher else "(not installed yet)")
              + f", as the signed-in user; anyone with '{cfg.launcher_access_profile_name}' can submit")
        if existing:
            print(f"note: the workflow '{cfg.plugin_workflow_name}' ({existing['id']}) is no longer used "
                  "(plugin.submit is \"launcher\"); `bulkaccess.py uninstall --only plugin` removes it with the plugin.")

    if a.dry_run:
        if body is not None:
            print(f"\n== Workflow ({'PUT ' + lib.WORKFLOWS + '/' + existing['id'] if existing else 'POST ' + lib.WORKFLOWS}) ==")
            print(definitions.pretty(body))
        elif not launcher:
            print(f"note: {lib.launcher_missing(cfg)}")
        runtime = lib.runtime_config(cfg, (existing or {}).get("id"), (launcher or {}).get("id", "<launcher-id>"))
        print(f"\n== {lib.RUNTIME_CONFIG} ==\n{definitions.pretty(runtime)}")
        print(f"\n== {lib.MANIFEST} ==\n{definitions.pretty(lib.manifest(cfg))}")
        if a.deploy:
            print(f"\n== Deploy (in {workdir}) ==\nnpm run build\n"
                  f"sail ui-plugins create|push-manifest {'' if public else '--private'}  (create only if alias {cfg.plugin_alias!r} is new)\n"
                  "sail ui-plugins upload")
        print("\nDry run: nothing was changed.")
        return 0

    # 1. Launcher (looked up) or workflow (always disabled)
    if via_launcher:
        if not launcher:
            raise TenantError(404, lib.launcher_missing(cfg))
        wf = None
    elif existing:
        if existing.get("enabled"):
            tenant.call("PATCH", f"{lib.WORKFLOWS}/{existing['id']}",
                        [{"op": "replace", "path": "/enabled", "value": False}],
                        content_type="application/json-patch+json")
        wf = tenant.call("PUT", f"{lib.WORKFLOWS}/{existing['id']}", body)
        print(f"Workflow updated: {wf['id']}  {cfg.plugin_workflow_name}  (disabled, mode {cfg.mode})")
    else:
        wf = tenant.call("POST", lib.WORKFLOWS, body)
        print(f"Workflow created: {wf['id']}  {cfg.plugin_workflow_name}  (disabled, mode {cfg.mode})")

    # 2. Runtime config + manifest, in the project that gets built
    lib.write_json(workdir / lib.RUNTIME_CONFIG,
                   lib.runtime_config(cfg, wf and wf["id"], launcher and launcher["id"]))
    lib.write_json(workdir / lib.MANIFEST, lib.manifest(cfg))
    print(f"Wrote {workdir / lib.RUNTIME_CONFIG} and {workdir / lib.MANIFEST}")

    # 3. Build and upload
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
        ui = tenant.base_url.replace(".api.", ".")
        print(f"Plugin deployed: {ui}/ui/plugin/{plugin.get('id', '<id>')}")

    print("\nDone." + ("" if cfg.live else "  (dry-run mode: approvals run, nothing is requested)")
          + (f"\nAnyone holding '{cfg.launcher_access_profile_name}' can submit from the plugin "
             f"(it uses the '{cfg.launcher_name}' Launcher as them)." if via_launcher else
             "\nOnly users who may test workflows (ORG_ADMIN) can submit from the plugin; "
             f"everyone else uses the '{cfg.launcher_name}' Launcher."))
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
