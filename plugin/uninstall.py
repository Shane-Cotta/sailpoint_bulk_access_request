#!/usr/bin/env python3
"""Remove what plugin/install.py created, and nothing else.

    python plugin/uninstall.py --config config/<tenant>.json [--yes] [--plugin]

Deletes the workflow named exactly "<prefix> Bulk Access Request (Plugin)" and,
with --plugin, the UI plugin instance whose alias and name match the config.
Each object is checked against the prefix before it is touched. Without --yes it
asks first. The Launcher deployment and anything else in the tenant is left alone.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pluginlib as lib
from pluginlib import TenantError, config_mod


def plan(cfg: lib.Config, workflow: dict | None, plugin: dict | None) -> list[tuple[str, str, str]]:
    """What would be removed, as (kind, id, name). Refuses anything that is not ours."""
    out = []
    if workflow:
        if not lib.owned_by_us(cfg, workflow.get("name")):
            raise TenantError(0, f"Refusing to delete workflow {workflow.get('name')!r}: not {cfg.plugin_workflow_name!r}.")
        out.append(("workflow", workflow["id"], workflow["name"]))
    if plugin:
        name = plugin.get("name")
        name = name.get("en") if isinstance(name, dict) else name
        if plugin.get("alias") != cfg.plugin_alias or not str(name or "").startswith(cfg.prefix):
            raise TenantError(0, f"Refusing to delete plugin {plugin.get('alias')!r} ({name!r}): "
                                 f"its name does not start with {cfg.prefix!r}.")
        out.append(("plugin", plugin["id"], f"{name} (alias {plugin['alias']})"))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True)
    ap.add_argument("--yes", action="store_true", help="don't ask for confirmation")
    ap.add_argument("--plugin", action="store_true", help="also delete the plugin instance (uses the SailPoint CLI)")
    ap.add_argument("--workdir", default=str(lib.PLUGIN_DIR), help="folder holding sp-ui-plugin.json (for --plugin)")
    a = ap.parse_args(argv)

    cfg = config_mod.load(a.config)
    tenant = lib.Tenant.from_env(cfg.env_file)
    workdir = Path(a.workdir)
    workflow = lib.find_workflow(tenant, cfg.plugin_workflow_name)
    plugin = lib.find_plugin(cfg, workdir) if a.plugin else None
    targets = plan(cfg, workflow, plugin)
    if not targets:
        print("Nothing to remove.")
        return 0

    print(f"Tenant {tenant.tenant_name}: will delete")
    for kind, oid, name in targets:
        print(f"  {kind:<8} {oid}  {name}")
    if not a.yes and input("Type 'yes' to continue: ").strip().lower() != "yes":
        print("Cancelled.")
        return 1

    for kind, oid, name in targets:
        if kind == "workflow":
            tenant.call("DELETE", f"{lib.WORKFLOWS}/{oid}")
        else:
            lib.sail(cfg, ["ui-plugins", "delete", oid, "--force"], workdir)
        print(f"Deleted {kind} {oid}  {name}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (config_mod.ConfigError, TenantError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
