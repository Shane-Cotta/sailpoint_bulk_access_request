#!/usr/bin/env python3
"""Remove the Launcher deployment from a tenant.

    python launcher/uninstall.py --config config/<tenant>.json            # shows what it would delete
    python launcher/uninstall.py --config config/<tenant>.json --yes      # deletes

Only deletes objects whose names this config produces (all start with the prefix):
the Launcher, the workflow, the form and the Launcher Access profile. If the plugin's Submit Access
role contains that profile, the profile is taken out of the role first (the role stays: its holders can
still submit from the plugin). Access that was already granted through the bulk requests is NOT revoked
(it is ordinary access now).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from bulkaccess import api, submit_access  # noqa: E402
from bulkaccess import config as config_mod  # noqa: E402
from bulkaccess.tenant import Tenant, TenantError  # noqa: E402
import install  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True)
    ap.add_argument("--yes", action="store_true", help="actually delete")
    a = ap.parse_args(argv)
    cfg = config_mod.load(a.config)
    t = Tenant.from_env(cfg.env_file)

    targets = []
    launcher = install.find_launcher(t, cfg.launcher_name)
    if launcher: targets.append(("Launcher", f"{api.LAUNCHERS}/{launcher['id']}", cfg.launcher_name))
    access = install.find_access_profile(t, cfg.launcher_access_profile_name)
    if access: targets.append(("Access profile", f"{api.ACCESS_PROFILES}/{access['id']}", access["name"]))
    wf = install.find_workflow(t, cfg.launcher_workflow_name)
    if wf: targets.append(("Workflow", f"{api.WORKFLOWS}/{wf['id']}", cfg.launcher_workflow_name))
    form = install.find_form(t, cfg.form_name)
    if form: targets.append(("Form", f"{api.FORM_DEFINITIONS}/{form['id']}", cfg.form_name))

    role = submit_access.find_named(t, api.ROLES, cfg.submit_role_name) if access else None
    if role and any(p.get("id") == access["id"] for p in role.get("accessProfiles") or []):
        keep = [p for p in role["accessProfiles"] if p.get("id") != access["id"]]
        print(f"{'UPDATE' if a.yes else 'would update'}  {'Role':15} {role['name']}  (without {access['name']})")
        if a.yes:
            t.call("PATCH", f"{api.ROLES}/{role['id']}", [{"op": "replace", "path": "/accessProfiles", "value": keep}],
                   content_type="application/json-patch+json")
    for kind, path, name in targets:
        assert name.startswith(cfg.prefix), f"refusing to delete {name!r}: it does not start with {cfg.prefix!r}"
        print(f"{'DELETE' if a.yes else 'would delete'}  {kind:15} {name}")
        if a.yes:
            if kind == "Workflow":
                t.call("PATCH", path, [{"op": "replace", "path": "/enabled", "value": False}],
                       content_type="application/json-patch+json")
            t.call("DELETE", path)
    if not targets:
        print("Nothing installed for this config.")
    elif not a.yes:
        print("\nRe-run with --yes to delete.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (config_mod.ConfigError, TenantError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr); sys.exit(1)
