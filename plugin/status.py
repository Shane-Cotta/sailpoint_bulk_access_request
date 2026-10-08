#!/usr/bin/env python3
"""Show what the UI plugin deployment has installed in a tenant (read-only).

    python plugin/status.py --config config/<tenant>.json [--executions 5] [--plugin]

Prints the plugin workflow (ID, enabled flag, mode, trigger), its latest
executions, pending "Bulk access …" approvals, and with --plugin the plugin
instance registered under the configured alias (needs the SailPoint CLI).
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
    ap.add_argument("--executions", type=int, default=5, help="how many recent executions to list")
    ap.add_argument("--plugin", action="store_true", help="also look up the plugin instance with the SailPoint CLI")
    ap.add_argument("--workdir", default=str(lib.PLUGIN_DIR), help="folder holding sp-ui-plugin.json (for --plugin)")
    a = ap.parse_args(argv)

    cfg = config_mod.load(a.config)
    tenant = lib.Tenant.from_env(cfg.env_file)
    print(f"Tenant {tenant.tenant_name} · prefix {cfg.prefix!r} · config mode {cfg.mode}")

    wf = lib.find_workflow(tenant, cfg.plugin_workflow_name)
    if not wf:
        print(f"Workflow: not installed ({cfg.plugin_workflow_name!r})")
    else:
        steps = (wf.get("definition") or {}).get("steps") or {}
        mode = "live" if "Request Access" in steps else "dry-run"
        print(f"Workflow: {wf['id']}  {wf['name']}")
        print(f"  enabled={wf.get('enabled')} (must be False)  trigger={wf.get('trigger', {}).get('type')}  "
              f"installed mode={mode}" + ("" if mode == cfg.mode else f"  <- config says {cfg.mode}; re-run install.py"))
        if a.executions:
            runs = tenant.call("GET", f"/v2025/workflows/{wf['id']}/executions?limit={a.executions}") or []
            print(f"  latest executions ({len(runs)}):")
            for run in runs:
                print(f"    {run.get('id')}  {run.get('status'):<9}  {run.get('startTime')}")

    approvals = tenant.call("GET", "/v2025/generic-approvals?limit=250") or []
    pending = [g for g in approvals if g.get("status") == "PENDING"
               and any(str(n.get("value", "")).startswith("Bulk access ") for n in g.get("name") or [])]
    print(f"Pending bulk approvals: {len(pending)}")
    for g in pending:
        g = tenant.call("GET", f"/v2025/generic-approvals/{g['id']}")   # the list leaves out the approvers
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
