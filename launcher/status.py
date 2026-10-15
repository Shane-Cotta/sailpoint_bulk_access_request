#!/usr/bin/env python3
"""Show what the Launcher deployment has installed in a tenant (read-only).

    python launcher/status.py --config config/<tenant>.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from bulkaccess import config as config_mod  # noqa: E402
from bulkaccess import api, definitions  # noqa: E402
from bulkaccess.tenant import Tenant, TenantError  # noqa: E402
import install  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True)
    a = ap.parse_args(argv)
    cfg = config_mod.load(a.config)
    t = Tenant.from_env(cfg.env_file)
    print(f"Tenant {t.tenant_name} · prefix {cfg.prefix!r} · config mode {cfg.mode}\n")

    ok = True
    form = install.find_form(t, cfg.form_name)
    if form:
        names = [i.get("name") for i in install.catalog_items(form)]
        temporary = install.form_element(form, "accessType") is not None
        print(f"[ok] Form      {form['id']}  {cfg.form_name}  ({len(names)} catalog item(s): "
              f"{', '.join(names[:5])}{'…' if len(names) > 5 else ''}; temporary access {'on' if temporary else 'off'})")
        if temporary != definitions.launcher_offers_temporary(cfg):
            print("     note: the form's temporary access fields differ from the config; re-run install.py.")
        if install.form_element(form, definitions.F_PART_LABEL) is not None:
            print("     note: the form has a hidden part field (older install), which makes the Launchpad show an "
                  "empty form; re-run install.py.")
    else:
        ok = False; print(f"[--] Form      missing: {cfg.form_name}")

    wf = install.find_workflow(t, cfg.launcher_workflow_name)
    if wf:
        full = t.call("GET", f"{api.WORKFLOWS}/{wf['id']}")
        live = "Request Access" in full["definition"]["steps"]
        manage = (full["definition"]["steps"].get("Request Access") or {}).get("attributes", {}).get("steps", {}).get("Manage Access", {})
        scoped = "filter.$" in (full.get("trigger") or {}).get("attributes", {})
        print(f"[{'ok' if full.get('enabled') and scoped else '!!'}] Workflow  {wf['id']}  {cfg.launcher_workflow_name}  "
              f"(enabled={full.get('enabled')}, installed mode={'live' if live else 'dry-run'}, trigger scoped={scoped})")
        if live != cfg.live:
            print("     note: installed mode differs from the config; re-run install.py to apply the config.")
        if live and manage.get("versionNumber") != 2:
            print("     note: Manage Access is not version 2 (no temporary access); re-run install.py.")
        ok &= bool(full.get("enabled")) and scoped
    else:
        ok = False; print(f"[--] Workflow  missing: {cfg.launcher_workflow_name}")

    launcher = install.find_launcher(t, cfg.launcher_name)
    if launcher:
        print(f"[{'ok' if not launcher.get('disabled') else '!!'}] Launcher  {launcher['id']}  {cfg.launcher_name}  (disabled={launcher.get('disabled')})")
        ok &= not launcher.get("disabled")
    else:
        ok = False; print(f"[--] Launcher  missing: {cfg.launcher_name}")

    access = install.find_access_profile(t, cfg.launcher_access_profile_name)
    as_wanted = bool(access) and bool(access.get("requestable")) == cfg.launcher_access_requestable
    print(f"[{'ok' if as_wanted else '--' if not access else '!!'}] Access    "
          f"{access['id'] if access else 'missing'}  {cfg.launcher_access_profile_name}"
          + ("" if not access else f"  (requestable={access.get('requestable')}"
             + (f"; people get it through the role '{cfg.submit_role_name}')" if cfg.deploy_plugin else ")")))
    ok &= as_wanted
    print("\nAll good." if ok else "\nSomething is missing or off; run launcher/install.py.")
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (config_mod.ConfigError, TenantError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr); sys.exit(1)
