#!/usr/bin/env python3
"""Show what the UI plugin deployment has installed in a tenant (read-only).

    python plugin/status.py --config config/<tenant>.json [--plugin]

Prints how the plugin submits (`plugin.submit`): through the Launcher (its ID), or
through the plugin workflow (ID, enabled flag, mode, trigger);
then pending "Bulk access …" approvals, and with --plugin the plugin instance
registered under the configured alias (needs the SailPoint CLI).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pluginlib as lib
from pluginlib import TenantError, config_mod


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True)
    ap.add_argument("--plugin", action="store_true", help="also look up the plugin instance with the SailPoint CLI")
    ap.add_argument("--workdir", default=str(lib.PLUGIN_DIR), help="folder holding sp-ui-plugin.json (for --plugin)")
    a = ap.parse_args(argv)

    cfg = config_mod.load(a.config)
    tenant = lib.Tenant.from_env(cfg.env_file)
    print(f"Tenant {tenant.tenant_name} · prefix {cfg.prefix!r} · config mode {cfg.mode}")

    wf = lib.find_workflow(tenant, cfg.plugin_workflow_name)
    if cfg.plugin_submits_via_launcher:
        launcher = lib.find_launcher(tenant, cfg.launcher_name)
        print(f"Submit: through the Launcher (plugin.submit \"launcher\"), as the signed-in user; "
              f"needs '{cfg.launcher_access_profile_name}'")
        print(f"Launcher: {launcher['id']}  {cfg.launcher_name}  (disabled={launcher.get('disabled')})" if launcher
              else f"Launcher: MISSING ({cfg.launcher_name!r}); the plugin can't submit. Run launcher/install.py, "
                   "then plugin/install.py.")
        print(f"Workflow: {wf['id']}  {wf['name']}  (not used in launcher mode; uninstall removes it)" if wf
              else "Workflow: none (not needed in launcher mode)")
    elif not wf:
        print(f"Workflow: not installed ({cfg.plugin_workflow_name!r})")
    else:
        steps = (wf.get("definition") or {}).get("steps") or {}
        mode = "live" if "Request Access" in steps else "dry-run"
        print(f"Workflow: {wf['id']}  {wf['name']}")
        print(f"  enabled={wf.get('enabled')} (must be False)  trigger={wf.get('trigger', {}).get('type')}  "
              f"installed mode={mode}" + ("" if mode == cfg.mode else f"  <- config says {cfg.mode}; re-run install.py"))

    approvals = tenant.call("GET", f"{lib.api.GENERIC_APPROVALS}?limit=250") or []
    pending = [g for g in approvals if g.get("status") == "PENDING"
               and any(str(n.get("value", "")).startswith("Bulk access ") for n in g.get("name") or [])]
    print(f"Pending bulk approvals: {len(pending)}")
    for g in pending:
        g = tenant.call("GET", f"{lib.api.GENERIC_APPROVALS}/{g['id']}")   # the list leaves out the approvers
        approvers = ", ".join(x.get("name", "?") for x in g.get("approvers") or [])
        print(f"  {g['id']}  {g['name'][0]['value']}  requester={g.get('requester', {}).get('name')}  approver={approvers or '?'}")

    if a.plugin:
        plugin = lib.find_plugin(cfg, Path(a.workdir))
        name = (plugin or {}).get("name")
        name = name.get("en") if isinstance(name, dict) else name
        print(f"Plugin instance: {plugin.get('id')}  alias {plugin.get('alias')}  name {name!r}  "
              f"bundle {plugin.get('activeAssetBundleId') or 'none (not uploaded)'}"
              if plugin else f"Plugin instance: none with alias {cfg.plugin_alias!r}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (config_mod.ConfigError, TenantError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
