#!/usr/bin/env python3
"""Show what the UI plugin deployment has installed in a tenant (read-only).

    python plugin/status.py --config config/<tenant>.json [--plugin]

Prints how the plugin submits (`plugin.submit`: directly, or through the Launcher and its ID), who may submit,
whether an old plugin workflow is still there, the pending item approvals of bulk requests (grouped by INC), and
with --plugin the plugin instance registered under the configured alias (needs the SailPoint CLI).
"""

from __future__ import annotations

import argparse
import sys
import urllib.parse
from collections import Counter
from pathlib import Path

import pluginlib as lib
from pluginlib import TenantError, config_mod
from bulkaccess import rules  # noqa: E402  (pluginlib put core on the path)

PENDING = 'status eq "PENDING" and type eq "ACCESS_REQUEST_APPROVAL"'


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True)
    ap.add_argument("--plugin", action="store_true", help="also look up the plugin instance with the SailPoint CLI")
    ap.add_argument("--workdir", default=str(lib.PLUGIN_DIR), help="folder holding sp-ui-plugin.json (for --plugin)")
    a = ap.parse_args(argv)

    cfg = config_mod.load(a.config)
    tenant = lib.Tenant.from_env(cfg.env_file)
    print(f"Tenant {tenant.tenant_name} · prefix {cfg.prefix!r} · config mode {cfg.mode}")

    if cfg.plugin_submits_via_launcher:
        launcher = lib.find_launcher(tenant, cfg.launcher_name)
        print(f"Submit: through the Launcher (plugin.submit \"launcher\"), as the signed-in user")
        print(f"Launcher: {launcher['id']}  {cfg.launcher_name}  (disabled={launcher.get('disabled')})" if launcher
              else f"Launcher: MISSING ({cfg.launcher_name!r}); the plugin can't submit. Run launcher/install.py, "
                   "then plugin/install.py.")
    else:
        allowed = lib.may_request_for_others(tenant)
        print("Submit: the plugin files the access requests itself (plugin.submit \"direct\"), as the signed-in user"
              + ("" if allowed else "; but this tenant doesn't let users request for others: re-run plugin/install.py, "
                                    "which then switches the plugin to the Launcher"))
    access = lib.find_access_profile(tenant, cfg.launcher_access_profile_name)
    print(f"Who may submit: holders of '{cfg.launcher_access_profile_name}' ({access['id']})" if access
          else f"Who may submit: anyone who can open the plugin ('{cfg.launcher_access_profile_name}' isn't installed)")
    old = lib.find_workflow(tenant, cfg.old_plugin_workflow_name)
    if old:
        print(f"Old plugin workflow: {old['id']}  {old['name']}  (no longer used; uninstall removes it)")

    rows = tenant.call("GET", f"{lib.api.GENERIC_APPROVALS}?include-comments=true&limit=250&filters="
                              + urllib.parse.quote(PENDING)) or []
    incs: Counter[str] = Counter()
    for row in rows:
        bulk = next((p for c in row.get("comments") or [] if (p := rules.parse_bulk_comment(cfg, c.get("comment")))),
                    None)
        if bulk:
            incs[bulk["inc"]] += 1
    print(f"Pending item approvals of bulk requests: {sum(incs.values())}"
          + (" (first 250 pending approvals checked)" if len(rows) == 250 else ""))
    for inc, n in sorted(incs.items()):
        print(f"  {inc}: {n}")

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
