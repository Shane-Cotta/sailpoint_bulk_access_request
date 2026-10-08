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
from bulkaccess import definitions  # noqa: E402
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
    else:
        ok = False; print(f"[--] Form      missing: {cfg.form_name}")

    wf = install.find_workflow(t, cfg.launcher_workflow_name)
    if wf:
        full = t.call("GET", f"/v2025/workflows/{wf['id']}")
        live = "Request Access" in full["definition"]["steps"]
        manage = (full["definition"]["steps"].get("Request Access") or {}).get("attributes", {}).get("steps", {}).get("Manage Access", {})
        scoped = "filter.$" in (full.get("trigger") or {}).get("attributes", {})
        print(f"[{'ok' if full.get('enabled') and scoped else '!!'}] Workflow  {wf['id']}  {cfg.launcher_workflow_name}  "
              f"(enabled={full.get('enabled')}, installed mode={'live' if live else 'dry-run'}, trigger scoped={scoped})")
        if live != cfg.live:
            print("     note: installed mode differs from the config; re-run install.py to apply the config.")
        if live and manage.get("versionNumber") != 2:
            print("     note: Manage Access is not version 2 (no temporary access); re-run install.py.")
        runs = t.call("GET", f"/v2025/workflows/{wf['id']}/executions?limit=5") or []
        for r in runs:
            print(f"     run {r.get('id')}  {r.get('status'):9}  {r.get('startTime', '')[:19]}")
        ok &= bool(full.get("enabled")) and scoped
    else:
        ok = False; print(f"[--] Workflow  missing: {cfg.launcher_workflow_name}")

    launcher = install.find_launcher(t, cfg.launcher_name)
    if launcher:
        print(f"[{'ok' if not launcher.get('disabled') else '!!'}] Launcher  {launcher['id']}  {cfg.launcher_name}  (disabled={launcher.get('disabled')})")
        ok &= not launcher.get("disabled")
    else:
        ok = False; print(f"[--] Launcher  missing: {cfg.launcher_name}")

    access = install.find_access_profile(t, f"{cfg.base_name} - Launcher Access")
    print(f"[{'ok' if access else '--'}] Access    {access['id'] if access else 'missing'}  {cfg.base_name} - Launcher Access"
          + ("" if not access else f"  (requestable={access.get('requestable')})"))
    ok &= bool(access)
    print("\nAll good." if ok else "\nSomething is missing or off; run launcher/install.py.")
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (config_mod.ConfigError, TenantError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr); sys.exit(1)
