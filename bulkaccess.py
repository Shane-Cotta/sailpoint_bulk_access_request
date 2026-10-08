#!/usr/bin/env python3
"""Bulk Access Request: one command for both deployments, driven by one config file.

    python bulkaccess.py show-config --config config/<tenant>.json
    python bulkaccess.py apply       --config config/<tenant>.json [--dry-run] [--only launcher|plugin] [--deploy [--workdir DIR]] [--grant me|<ids>]
    python bulkaccess.py status      --config config/<tenant>.json [--only launcher|plugin]
    python bulkaccess.py uninstall   --config config/<tenant>.json [--only launcher|plugin] [--yes]
    python bulkaccess.py export      --config config/<tenant>.json [--only launcher|plugin] [--offline] [--out DIR]

Only the deployments enabled under `deployments` in the config are acted on. Each one is
run by its own script (launcher/install.py, plugin/install.py, ...), which still works on
its own; this command just calls their main() in turn.

  show-config  what the config means for each route (people limit, parts, temporary access)
  apply        install or update; --dry-run prints every body and changes nothing,
               --deploy also builds and uploads the plugin, --grant gives Launcher access
  status       what is installed (read-only)
  uninstall    removes what apply created (asks first; --yes to delete)
  export       writes the workflows, form, Launcher and access profile as JSON files: as installed
               in the tenant (read-only), or with --offline as this code would build them
"""

from __future__ import annotations

import argparse
import builtins
import importlib.util
import json
import sys
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Iterator

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "core"))   # before the repo root, so `bulkaccess` is the core package, not this file

from bulkaccess import config as config_mod  # noqa: E402
from bulkaccess import definitions  # noqa: E402
from bulkaccess.config import FORM_SELECT_MAX, Config  # noqa: E402
from bulkaccess.tenant import TenantError  # noqa: E402

# Script-local module names: both folders have an install.py, status.py and uninstall.py.
_LOCAL_MODULES = ("install", "status", "uninstall", "e2e", "pluginlib")


# ───────────────────────────────────────────────────────────── show-config ──
def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def people_summary(cfg: Config) -> str:
    """e.g. "no limit, sent as approvals of up to 250 (plugin) · up to 30 (Launcher form, SailPoint limit)"."""
    parts = []
    if cfg.deploy_plugin:
        size = cfg.part_size
        if cfg.people_max is None:
            parts.append(f"no limit, sent as approvals of up to {size} (plugin)")
        elif cfg.people_max <= size:
            parts.append(f"up to {cfg.people_max}, in one approval (plugin)")
        else:
            parts.append(f"up to {cfg.people_max}, sent as approvals of up to {size} (plugin)")
    if cfg.deploy_launcher:
        cap = cfg.launcher_people_cap
        capped = cfg.people_max is None or cfg.people_max > FORM_SELECT_MAX
        parts.append(f"up to {cap} (Launcher form{', SailPoint limit' if capped else ''})")
    return " · ".join(parts)


def _units(units) -> str:
    return ", ".join(u.lower() for u in units)


def temporary_summary(cfg: Config, route: str) -> str:
    if route == "launcher":
        units = definitions.launcher_duration_units(cfg)
        if not units:
            return "off" + (" (temporaryAccess.allow has no duration)" if "duration" not in cfg.temporary_allow else
                            " (no unit fits maxDays)" if cfg.temporary_units else "")
        dropped = [u for u in cfg.temporary_units if u not in units]
        text = f"duration in {_units(units)}"
        if "endDate" in cfg.plugin_temporary_modes:
            text += " (no end date: a workflow can't turn a date into a duration)"
        if dropped:
            text += f"; {_units(dropped)} not offered (longer than {cfg.temporary_max_days} days)"
        return text
    modes = cfg.plugin_temporary_modes
    if not modes:
        return "off"
    out = []
    if "duration" in modes:
        out.append(f"duration in {_units(cfg.temporary_units)}")
    if "endDate" in modes:
        out.append("end date (sent as hours)")
    return " or ".join(out)


APPROVALS_PRIVATE_WARNING = ("non-admin approvers can't open a private plugin unless they are listed in the plugin's "
                             "restrictToUsers")


def approvals_summary(cfg: Config) -> str:
    """The plugin's Approvals tab, in one line."""
    if not cfg.deploy_plugin:
        return "off (the Approvals tab is part of the plugin, which is off)"
    if not cfg.approvals_enabled:
        return "off (approvals.enabled is false)"
    endpoint = {"auto": "bulk endpoint when allowed", "always": "always the bulk endpoint",
                "never": "one call per approval"}[cfg.approvals_use_bulk_endpoint]
    return (f"Approvals tab in the plugin: {cfg.approvals_concurrency} at a time, {endpoint}, "
            f"up to {cfg.approvals_max_rows} pending rows"
            + (", other approvals shown" if cfg.approvals_show_other else "")
            + ("; a denial needs a comment" if cfg.approvals_deny_comment_required else ""))


def describe(cfg: Config) -> list[str]:
    """What will actually be used, route by route (the `show-config` output)."""
    routes = cfg.deployments
    lines = [
        f"Config:      {cfg.source_path or '(in memory)'}",
        f"Prefix:      {cfg.prefix}  ·  mode {cfg.mode}"
        + ("" if cfg.live else " (approvals run, nothing is requested)"),
        f"Deployments: {', '.join(routes)}"
        + "".join(f"  ({d} off)" for d in config_mod.DEPLOYMENTS if d not in routes),
    ]
    if cfg.deploy_launcher:
        lines.append(f"  launcher:  form '{cfg.form_name}', workflow '{cfg.launcher_workflow_name}', "
                     f"Launcher '{cfg.launcher_name}', access profile '{cfg.base_name} - Launcher Access' "
                     f"(approval: {'manager' if cfg.launcher_access_approval == 'MANAGER' else 'none, auto-approved'})")
    if cfg.deploy_plugin:
        lines.append(f"  plugin:    workflow '{cfg.plugin_workflow_name}' (disabled), plugin '{cfg.plugin_display_name}' "
                     f"(alias {cfg.plugin_alias}, {'visible to everyone' if cfg.plugin_public else 'private'})")
    lines += [
        f"INC:         {cfg.inc_pattern}  e.g. {cfg.inc_example}",
        f"Catalog:     {', '.join(cfg.catalog_types)}"
        + (f", names starting with '{cfg.catalog_name_starts_with}'" if cfg.catalog_name_starts_with else "")
        + f"; up to {cfg.catalog_max_items} items per request",
        f"People:      {people_summary(cfg)}",
        "Temporary:   " + ("off (temporaryAccess.enabled is false)" if not cfg.temporary_enabled else
                           (f"at most {cfg.temporary_max_days} days" if cfg.temporary_max_days else "no maximum")),
    ]
    if cfg.temporary_enabled:
        for route in routes:
            lines.append(f"  {route + ':':10} {temporary_summary(cfg, route)}")
    lines += [
        f"Approval:    one approver; expires after {_plural(cfg.approval_timeout_days, 'day')} "
        f"({cfg.approval_action_at_timeout}), priority {cfg.approval_priority}",
        "Email:       " + (f"all mail goes to {', '.join(cfg.override_recipients)} (override)" if cfg.override_recipients
                           else "the requester" + (", cc the approver" if cfg.cc_approver else "")),
    ]
    lines.append("Approvals:   " + approvals_summary(cfg))
    if cfg.plugin_approvals_enabled and not cfg.plugin_public:
        lines.append(f"Warning:     {APPROVALS_PRIVATE_WARNING}")
    lines.append(f"Owner:       {cfg.owner_id}" if cfg.owner_id else f"Note:        {config_mod.OWNER_NOTE}")
    for note in cfg.deprecations:
        lines.append(f"Deprecated:  {note}")
    return lines


# ─────────────────────────────────────────────────────── running a script ──
@contextmanager
def _script_dir(folder: Path) -> Iterator[None]:
    """Import context for one deployment folder: its directory first on sys.path, and none of
    the other folder's same-named modules (install, status, ...) left in sys.modules."""
    saved_path = list(sys.path)
    saved = {name: sys.modules.pop(name) for name in _LOCAL_MODULES if name in sys.modules}
    sys.path.insert(0, str(folder))
    try:
        yield
    finally:
        sys.path[:] = saved_path
        for name in _LOCAL_MODULES:
            sys.modules.pop(name, None)
        sys.modules.update(saved)


def load_script(deployment: str, script: str) -> ModuleType:
    path = ROOT / deployment / f"{script}.py"
    spec = importlib.util.spec_from_file_location(f"bulkaccess_{deployment}_{script}", path)
    if spec is None or spec.loader is None:
        raise TenantError(0, f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_script(deployment: str, script: str, argv: list[str], *, answer: str | None = None) -> int:
    """Run <deployment>/<script>.py main(argv). `answer` replies to an input() prompt (preview mode)."""
    print(f"\n━━ {deployment}: {script}.py {' '.join(argv)}")
    sys.stdout.flush()
    with _script_dir(ROOT / deployment):
        module = load_script(deployment, script)
        if answer is None:
            return int(module.main(argv) or 0)
        real_input = builtins.input
        builtins.input = lambda prompt="": (print(f"{prompt}{answer}") or answer)
        try:
            return int(module.main(argv) or 0)
        finally:
            builtins.input = real_input


def targets(cfg: Config, only: str | None) -> list[str]:
    if only and only not in cfg.deployments:
        raise config_mod.ConfigError(f"`--only {only}`: the {only} deployment is switched off in `deployments`.")
    return [only] if only else list(cfg.deployments)


# ──────────────────────────────────────────────────────────────── commands ──
def cmd_show_config(cfg: Config, a: argparse.Namespace) -> int:
    print("\n".join(describe(cfg)))
    return 0


def cmd_apply(cfg: Config, a: argparse.Namespace) -> int:
    base = ["--config", a.config] + (["--dry-run"] if a.dry_run else [])
    chosen = targets(cfg, a.only)
    if a.grant and "launcher" not in chosen:
        print("note: --grant only applies to the Launcher deployment; ignored.")
    if a.deploy and "plugin" not in chosen:
        print("note: --deploy only applies to the plugin deployment; ignored.")
    rc = 0
    for d in chosen:
        if d == "launcher":
            rc |= run_script("launcher", "install", base + (["--grant", a.grant] if a.grant else []))
        else:
            rc |= run_script("plugin", "install", base + (["--deploy"] if a.deploy else [])
                             + (["--workdir", a.workdir] if a.workdir else [])
                             + (["--public"] if cfg.plugin_public else []))
    return rc


def cmd_status(cfg: Config, a: argparse.Namespace) -> int:
    rc = 0
    for d in targets(cfg, a.only):
        rc |= run_script(d, "status", ["--config", a.config])
    return rc


def cmd_uninstall(cfg: Config, a: argparse.Namespace) -> int:
    rc = 0
    for d in targets(cfg, a.only):
        if d == "launcher":
            rc |= run_script("launcher", "uninstall", ["--config", a.config] + (["--yes"] if a.yes else []))
            continue
        argv = ["--config", a.config] + (["--yes"] if a.yes else [])
        try:   # also remove the plugin instance; that needs the SailPoint CLI
            rc |= run_script("plugin", "uninstall", argv + ["--plugin"], answer=None if a.yes else "no")
        except (FileNotFoundError, TenantError) as exc:
            print(f"note: could not look up the plugin instance ({str(exc).splitlines()[0]}); "
                  "removing the workflow only. Delete the plugin with `sail ui-plugins delete` if it exists.")
            rc |= run_script("plugin", "uninstall", argv, answer=None if a.yes else "no")
    if not a.yes:
        print("\nNothing was deleted. Re-run with --yes to delete.")
        return 0
    return rc


# ────────────────────────────────────────────────────────────────── export ──
# Fields SailPoint fills in itself. They're dropped so exports diff cleanly between runs and
# can be imported elsewhere (Admin > Workflows > Import, or POST /v2025/workflows).
_SERVER_FIELDS = ("id", "created", "modified", "creator", "modifiedBy", "executionCount", "failureCount",
                  "dailyExecutionCount", "usedBy", "tenantId")
_PLACEHOLDER = {"owner": "<OWNER_IDENTITY_ID>", "form": "<FORM_DEFINITION_ID>", "workflow": "<WORKFLOW_ID>",
                "entitlement": "<ASSIGNED_LAUNCHERS_ENTITLEMENT_ID>", "source": "<IDENTITYNOW_SOURCE_ID>"}


def _clean(obj: dict | None) -> dict | None:
    return None if obj is None else {k: v for k, v in obj.items() if k not in _SERVER_FIELDS}


def offline_objects(cfg: Config, only: str | None = None) -> dict[str, dict]:
    """What `apply` would create, built without a tenant. IDs only known after install are placeholders."""
    out: dict[str, dict] = {}
    for d in targets(cfg, only):
        if d == "launcher":
            out["launcher/form.json"] = definitions.bulk_form(cfg, _PLACEHOLDER["owner"], [])
            out["launcher/workflow.json"] = definitions.bulk_workflow(
                cfg, variant="launcher", owner_id=_PLACEHOLDER["owner"], form_id=_PLACEHOLDER["form"],
                workflow_id=_PLACEHOLDER["workflow"])
            out["launcher/launcher.json"] = definitions.bulk_launcher(cfg, _PLACEHOLDER["workflow"])
            out["launcher/access-profile.json"] = definitions.launcher_access_profile(
                cfg, _PLACEHOLDER["owner"], {"id": _PLACEHOLDER["entitlement"], "name": cfg.launcher_name,
                                             "source": {"id": _PLACEHOLDER["source"], "name": "IdentityNow"}})
        else:
            out["plugin/workflow.json"] = definitions.bulk_workflow(cfg, variant="plugin", owner_id=_PLACEHOLDER["owner"])
    return out


def tenant_objects(cfg: Config, only: str | None = None) -> tuple[str, dict[str, dict | None]]:
    """What is installed in the tenant (read-only lookups by name)."""
    from bulkaccess.tenant import Tenant
    t = Tenant.from_env(cfg.env_file)
    with _script_dir(ROOT / "launcher"):
        lib = load_script("launcher", "install")
    out: dict[str, dict | None] = {}
    for d in targets(cfg, only):
        if d == "launcher":
            out["launcher/form.json"] = lib.find_form(t, cfg.form_name)
            out["launcher/workflow.json"] = lib.find_workflow(t, cfg.launcher_workflow_name)
            out["launcher/launcher.json"] = lib.find_launcher(t, cfg.launcher_name)
            out["launcher/access-profile.json"] = lib.find_access_profile(t, f"{cfg.base_name} - Launcher Access")
        else:
            out["plugin/workflow.json"] = lib.find_workflow(t, cfg.plugin_workflow_name)
    return t.tenant_name, {k: _clean(v) for k, v in out.items()}


def cmd_export(cfg: Config, a: argparse.Namespace) -> int:
    if a.offline:
        where, objects = "offline", dict(offline_objects(cfg, a.only))
    else:
        where, objects = tenant_objects(cfg, a.only)
    out = Path(a.out) if a.out else ROOT / "exports" / f"{cfg.prefix.lower()}-{where}"
    missing = [name for name, body in objects.items() if body is None]
    for name, body in objects.items():
        if body is None:
            continue
        path = out / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(body, indent=2) + "\n")
        print(f"wrote {path}")
    for name in missing:
        print(f"not installed, skipped: {name}  (run `bulkaccess.py apply` first)")
    if a.offline:
        print("Offline export: IDs that only exist after install are placeholders like <FORM_DEFINITION_ID>; "
              "the form has no catalog items (apply fills them from the tenant).")
    return 0 if objects and len(missing) < len(objects) else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bulkaccess.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    def command(name: str, help_text: str, only: bool = True) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_text, description=help_text)
        p.add_argument("--config", required=True, help="config/<tenant>.json")
        if only:
            p.add_argument("--only", choices=config_mod.DEPLOYMENTS, help="act on one deployment only")
        return p

    command("show-config", "Show what the config means for each deployment.", only=False)
    p = command("apply", "Install or update the enabled deployments.")
    p.add_argument("--dry-run", action="store_true", help="print every body; change nothing")
    p.add_argument("--deploy", action="store_true", help="plugin: also build and upload it with the SailPoint CLI")
    p.add_argument("--grant", default="", help="Launcher: comma-separated identity IDs (or 'me') to give Launcher access")
    p.add_argument("--workdir", default="", help="plugin: Angular project to build and upload (default: plugin/)")
    command("status", "Show what is installed (read-only).")
    p = command("uninstall", "Remove what apply created.")
    p.add_argument("--yes", action="store_true", help="actually delete (otherwise only shows what would go)")
    p = command("export", "Write the workflows, form, Launcher and access profile as JSON files.")
    p.add_argument("--offline", action="store_true", help="build them from this code and config, without a tenant")
    p.add_argument("--out", default="", help="folder to write to (default: exports/<prefix>-<tenant|offline>/)")

    a = ap.parse_args(argv)
    cfg = config_mod.load(a.config)
    handler = {"show-config": cmd_show_config, "apply": cmd_apply, "status": cmd_status, "uninstall": cmd_uninstall,
               "export": cmd_export}
    return handler[a.command](cfg, a)


if __name__ == "__main__":
    import subprocess
    try:
        sys.exit(main())
    except (config_mod.ConfigError, TenantError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError as exc:   # npm or sail missing
        print(f"ERROR: {exc}. Install Node.js/npm and the SailPoint CLI (sail 2.7+).", file=sys.stderr)
        sys.exit(1)
