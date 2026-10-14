#!/usr/bin/env python3
"""Reassign object ownership on termination: install, test and manage the SailPoint ISC workflow.

  python ownership.py doctor    --config config/<tenant>.json   # read-only preflight: is this tenant ready?
  python ownership.py apply     --config config/<tenant>.json --dry-run
  python ownership.py apply     --config config/<tenant>.json [--enable]
  python ownership.py test      --config config/<tenant>.json --departed <id|username|email> [--keep]
  python ownership.py status    --config config/<tenant>.json
  python ownership.py uninstall --config config/<tenant>.json [--yes]
  python ownership.py export    [--config config/<tenant>.json] [--out FILE|-]   # JSON with placeholders

Credentials: SAIL_BASE_URL, SAIL_CLIENT_ID, SAIL_CLIENT_SECRET (an ORG_ADMIN PAT) in the environment or in the
config's envFile. Standard library only (Python 3.10+).
"""

from __future__ import annotations

import argparse
import dataclasses
import os
import sys
import uuid
from pathlib import Path
from typing import Any

from reassign import api
from reassign import config as config_mod
from reassign import ops
from reassign import workflow as wf
from reassign.tenant import Tenant, TenantError

EXPORT_PATH = Path(__file__).parent / "workflow" / "reassign-ownership-on-termination.json"


def _tenant(cfg: config_mod.Config) -> Tenant:
    return Tenant.from_env(cfg.env_file)


def _target(tenant: Tenant, cfg: config_mod.Config) -> tuple[wf.Target, dict, dict, dict]:
    """Real values for the workflow: the resolved fallback and workflow owners, the API URL, and the Parameter
    Storage credential the HTTP steps use (referenced by ID; the workflow itself holds no secret)."""
    if not cfg.fallback_owner:
        raise config_mod.ConfigError("fallbackOwner is required: an identity (ID, username or email) who receives "
                                     "the objects when there's no usable manager")
    fallback = ops.resolve_identity(tenant, cfg.fallback_owner)
    owner = ops.resolve_identity(tenant, cfg.workflow_owner) if cfg.workflow_owner else tenant.me()
    param = ops.find_parameter(tenant, cfg.parameter)
    target = wf.Target(api_url=ops.api_url(tenant), fallback_owner_id=fallback["id"], workflow_owner_id=owner["id"],
                       workflow_owner_name=owner.get("name"), parameter_id=param["id"])
    return target, fallback, owner, param


# ── doctor ──────────────────────────────────────────────────────────────────
def cmd_doctor(cfg: config_mod.Config, _a: argparse.Namespace) -> int:
    problems: list[str] = []
    warnings: list[str] = []

    def ok(msg: str) -> None:
        print(f"  ok    {msg}")

    def bad(msg: str) -> None:
        problems.append(msg)
        print(f"  FAIL  {msg}")

    def warn(msg: str) -> None:
        warnings.append(msg)
        print(f"  warn  {msg}")

    tenant = _tenant(cfg)
    print(f"Tenant {tenant.tenant_name} ({tenant.base_url}) · config {cfg.path}")

    claims = tenant.claims()
    if "ORG_ADMIN" in (claims.get("authorities") or []):
        ok(f"PAT user {claims.get('user_name')} is ORG_ADMIN")
    else:
        bad(f"PAT user {claims.get('user_name')} is not ORG_ADMIN (needed to install, test and PATCH owners)")
    try:
        param = ops.find_parameter(tenant, cfg.parameter)
        pub = param.get("publicFields") or {}
        expected_token_url = f"{ops.api_url(tenant)}/oauth/token"
        if pub.get("tokenURL") != expected_token_url:
            bad(f"parameter {param['name']!r}: Token URL is {pub.get('tokenURL')!r}, expected {expected_token_url!r}")
        else:
            ok(f"parameter {param['name']!r} ({param['id']}): OAuth client {pub.get('clientId')}, token URL ok")
        if pub.get("clientId") == os.environ.get("SAIL_CLIENT_ID"):
            warn("the workflow's parameter holds the installer's own PAT. For production, give the workflow a "
                 "dedicated ORG_ADMIN PAT (its owner should be a service identity).")
    except TenantError as exc:
        bad(str(exc))

    triggers = tenant.call("GET", f"{api.WORKFLOW_LIBRARY}/triggers?limit=250") or []
    if any(t.get("id") == wf.TRIGGER_ID for t in triggers):
        ok(f"trigger {wf.TRIGGER_ID} available")
    else:
        bad(f"trigger {wf.TRIGGER_ID} not offered by this tenant")
    actions = tenant.call("GET", f"{api.WORKFLOW_LIBRARY}/actions?limit=250") or []
    for aid, version in (("sp:http", 3), ("sp:get-identity", 2), ("sp:loop:iterator", 1), ("sp:send-email", 2)):
        if any(x.get("id") == aid and x.get("versionNumber") == version for x in actions):
            ok(f"action {aid} v{version} available")
        else:
            bad(f"action {aid} v{version} not offered by this tenant")

    flt = wf.trigger_filter(cfg.termination_states)
    for state, want in ((cfg.termination_states[0], True), ("active" if "active" not in cfg.termination_states else "x", False)):
        sample = wf.test_input("0" * 32, "sample", state)
        try:
            res = tenant.call("POST", f"{api.TRIGGER_SUBSCRIPTIONS}/validate-filter", {"input": sample, "filter": flt})
            matched = bool((res or {}).get("isValid"))
            (ok if matched == want else bad)(f"trigger filter {'matches' if matched else 'skips'} newLifecycleState={state!r}")
        except TenantError as exc:
            warn(f"could not validate the trigger filter ({exc})")
            break

    profiles = tenant.call("GET", f"{api.IDENTITY_PROFILES}?limit=250") or []
    seen: dict[str, list[str]] = {s: [] for s in cfg.termination_states}
    enabled: dict[str, list[str]] = {s: [] for s in cfg.termination_states}
    for p in profiles:
        for st in tenant.call("GET", f"{api.IDENTITY_PROFILES}/{p['id']}/lifecycle-states") or []:
            name = st.get("technicalName")
            if name in seen:
                seen[name].append(p["name"])
                if st.get("enabled"):
                    enabled[name].append(p["name"])
    for s in cfg.termination_states:
        if not seen[s]:
            bad(f"lifecycle state {s!r} exists in no identity profile (check terminationStates: technical names)")
        elif not enabled[s]:
            warn(f"lifecycle state {s!r} is disabled in every identity profile ({', '.join(seen[s])}): "
                 "the workflow will never fire until one enables it")
        else:
            ok(f"lifecycle state {s!r} enabled in: {', '.join(enabled[s])}")

    if not cfg.fallback_owner:
        bad("fallbackOwner is not set")
    else:
        try:
            fb = ops.resolve_identity(tenant, cfg.fallback_owner)
            state = ops.lifecycle_state(fb)
            if state in cfg.inactive_states or state in cfg.termination_states:
                bad(f"fallback owner {fb['name']} is in lifecycle state {state!r}")
            else:
                ok(f"fallback owner {fb['name']} ({fb['id']}), lifecycle state {state or 'none'}")
        except TenantError as exc:
            bad(f"fallback owner {cfg.fallback_owner!r}: {exc}")
    if cfg.workflow_owner:
        try:
            wo = ops.resolve_identity(tenant, cfg.workflow_owner)
            ok(f"workflow owner {wo['name']} ({wo['id']})")
        except TenantError as exc:
            bad(f"workflow owner {cfg.workflow_owner!r}: {exc}")
    else:
        warn(config_mod.WORKFLOW_OWNER_NOTE)

    for key in cfg.object_types:
        ot = wf.OBJECT_TYPES[key]
        try:
            if ot.server_filter:
                ops.owned(tenant, ot, "0" * 32, limit=1)
                ok(f"{ot.plural}: GET {ot.path}?filters=owner.id eq … answers")
            else:
                n = ops.count(tenant, ot)
                (ok if n <= wf.PAGE else warn)(
                    f"{ot.plural}: {n} in the tenant" + ("" if n <= wf.PAGE else
                    f"; the workflow only checks the first {wf.PAGE} (this list can't filter on owner)"))
        except TenantError as exc:
            bad(f"{ot.plural}: GET {ot.path}: {exc}")
    if cfg.pending_approvals:
        try:
            ops.pending_approvals(tenant, "0" * 32, limit=1)
            ok(f"Pending approvals: GET {api.GENERIC_APPROVALS}?approverId=… answers")
        except TenantError as exc:
            bad(f"Pending approvals: GET {api.GENERIC_APPROVALS}: {exc}")
    if cfg.pending_certifications:
        try:
            # A reviewer-identity that doesn't exist is a 404 (seen live), so ask about a real one: the PAT user.
            ops.open_certifications(tenant, tenant.me()["id"], limit=1)
            ok(f"Open certifications: GET {api.CERTIFICATIONS}?reviewer-identity=… answers")
        except TenantError as exc:
            bad(f"Open certifications: GET {api.CERTIFICATIONS}: {exc}")

    # SailPoint's ISC Governance connector can reassign on its own, to the manager, when the leaver's governance
    # account is disabled; two reassigners on one termination race each other (README: Alongside SailPoint's tools).
    try:
        sources = ops.governance_connector_sources(tenant)
    except TenantError as exc:
        sources = []
        warn(f"could not look for ISC Governance connector sources ({exc})")
    if not sources:
        ok("no ISC Governance connector source, so nothing else reassigns on termination")
    ours = set(cfg.object_types) | {k for k, on in (("pendingApprovals", cfg.pending_approvals),
                                                     ("pendingCertifications", cfg.pending_certifications)) if on}
    for src in sources:
        attrs = src.get("connectorAttributes") or {}
        on = [k for k in ops.GOVERNANCE_REASSIGN_KEYS if attrs.get(k) is True]
        clash = [k for k in on if ops.GOVERNANCE_REASSIGN_KEYS[k] in ours]
        if clash:
            warn(f"ISC Governance connector source {src.get('name')!r} also reassigns {', '.join(clash)} (to the "
                 "manager, when the account is disabled). Turn those off there, or the matching types off here")
        else:
            ok(f"ISC Governance connector source {src.get('name')!r}: none of its reassignment settings overlap"
               + (f" (it does {', '.join(on)})" if on else ""))

    print(f"\n{len(problems)} problem(s), {len(warnings)} warning(s).")
    return 1 if problems else 0


# ── export / apply ──────────────────────────────────────────────────────────
def cmd_export(cfg: config_mod.Config | None, a: argparse.Namespace) -> int:
    text = wf.pretty(wf.build(cfg or config_mod.defaults()))
    if a.out == "-":
        sys.stdout.write(text)
    else:
        out = Path(a.out) if a.out else EXPORT_PATH
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
        print(f"Wrote {out} (placeholders: {', '.join(wf.PLACEHOLDERS)})")
    return 0


def cmd_apply(cfg: config_mod.Config, a: argparse.Namespace) -> int:
    tenant = _tenant(cfg)
    target, fallback, owner, param = _target(tenant, cfg)
    print(f"Tenant {tenant.tenant_name} · workflow {cfg.workflow_name!r}")
    print(f"Fallback owner: {fallback['name']} ({fallback['id']}) · workflow owner: {owner.get('name')} "
          f"· strategy {cfg.owner_strategy} · triggers on {list(cfg.termination_states)} · types {list(cfg.object_types)}")
    if not cfg.workflow_owner:
        print(f"note: {config_mod.WORKFLOW_OWNER_NOTE}")
    print(f"HTTP steps authenticate with Parameter Storage parameter {param['name']!r} ({param['id']}).")
    existing = ops.find_workflow(tenant, cfg.workflow_name)
    # Keep the installed HTTP steps' parameter references (refIDs); steps that are new get fresh ones.
    kept = wf.ref_ids_of(existing) if existing else {}
    target = dataclasses.replace(target, ref_ids={n: kept.get(n) or str(uuid.uuid4()) for n in wf.http_step_names(cfg)})
    body = wf.build(cfg, target)
    if a.dry_run:
        print(f"\n== Workflow ({'update ' + existing['id'] if existing else 'create'}) ==\n{wf.pretty(body)}")
        print("Dry run: nothing was changed.")
        return 0
    if existing:
        ops.put_workflow(tenant, existing["id"], body, enable=a.enable)
        wid = existing["id"]
        print(f"Workflow updated: {wid}")
    else:
        # Create, then PUT the same body: on create ISC drops the HTTP steps' parameter links (paramID/refID);
        # an update keeps them and registers each reference against the workflow's ID (CLAUDE.md).
        wid = tenant.call("POST", ops.WORKFLOWS, body)["id"]
        ops.put_workflow(tenant, wid, body, enable=a.enable)
        print(f"Workflow created: {wid}")
    linked = wf.ref_ids_of(tenant.call("GET", f"{ops.WORKFLOWS}/{wid}"))
    missing = sorted(set(wf.http_step_names(cfg)) - set(linked))
    if missing:
        print(f"WARNING: these HTTP steps lost their Parameter Storage link: {', '.join(missing)}. "
              "Open them in the workflow builder and pick the parameter.")
    print("Enabled: it now runs on every matching lifecycle change." if a.enable else
          "Left disabled. Test it (ownership.py test), then run apply --enable.")
    return 0


# ── test ────────────────────────────────────────────────────────────────────
def _expected_owner(tenant: Tenant, cfg: config_mod.Config, departed: dict, fallback: dict) -> tuple[str, str]:
    """What the workflow should choose, computed independently: (owner ID, 'manager' | 'fallback')."""
    mgr_id = (departed.get("managerRef") or {}).get("id")
    if cfg.owner_strategy == "manager-then-fallback" and mgr_id:
        mgr = tenant.call("GET", f"{ops.IDENTITIES}/{mgr_id}")
        if ops.lifecycle_state(mgr) not in cfg.inactive_states:
            return mgr_id, "manager"
    return fallback["id"], "fallback"


def cmd_test(cfg: config_mod.Config, a: argparse.Namespace) -> int:
    tenant = _tenant(cfg)
    existing = ops.find_workflow(tenant, cfg.workflow_name)
    if not existing:
        print(f"Workflow {cfg.workflow_name!r} not found: run apply first.")
        return 1
    if existing.get("enabled"):
        print("The workflow is enabled; the test endpoint only runs disabled workflows. Disable it first "
              "(apply without --enable) and enable it again after testing.")
        return 1
    departed = ops.resolve_identity(tenant, a.departed)
    fallback = ops.resolve_identity(tenant, cfg.fallback_owner or "")
    want_id, why = _expected_owner(tenant, cfg, departed, fallback)
    expect_failure = want_id == departed["id"]
    print(f"Departed (simulated): {departed['name']} ({departed['id']}); manager "
          f"{(departed.get('managerRef') or {}).get('name') or 'none'}")
    print("Expected: the run stops with 'No usable new owner' and nothing changes." if expect_failure else
          f"Expected: everything goes to {why} {want_id}.")

    prefix = cfg.test_object_prefix
    leftovers = ops.find_test_objects(tenant, prefix)
    if leftovers:
        print(f"Removing test objects left from an earlier run: {', '.join(o['name'] for o in leftovers.values())}")
        problems = ops.delete_test_objects(tenant, leftovers, prefix)
        if problems:
            print("\n".join(problems))
            return 1

    # Safety: the workflow reassigns everything the departed identity owns, so only run it on someone who owns
    # nothing yet (the test then creates the only objects it can touch).
    already = {k: [o["name"] for o in v] for k, v in ops.owned_all(tenant, departed["id"]).items() if v}
    if already:
        print(f"Refusing: {departed['name']} already owns {already}. Test with an identity who owns nothing.")
        return 1

    old_campaign = ops.find_test_campaign(tenant, prefix)
    if old_campaign:
        print(f"Removing the test campaign left from an earlier run: {old_campaign['name']}")
        ops.delete_test_campaign(tenant, old_campaign, prefix)
    if cfg.pending_approvals:
        theirs = ops.pending_approvals(tenant, departed["id"])
        if theirs:
            print(f"Refusing: {departed['name']} has {len(theirs)} pending approval(s), which the workflow would move. "
                  "Test with an identity who has none.")
            return 1
    if cfg.pending_certifications:
        theirs = ops.open_certifications(tenant, departed["id"])
        if theirs:
            print(f"Refusing: {departed['name']} reviews {len(theirs)} open certification(s), which the workflow "
                  "would move. Test with an identity who has none.")
            return 1
    if a.pending_for and (a.no_objects or not (cfg.pending_approvals or cfg.pending_certifications)):
        print("--pending-for needs the test objects and pendingApprovals or pendingCertifications on.")
        return 1
    requestee = ops.resolve_identity(tenant, a.pending_for) if a.pending_for else None

    made = {} if a.no_objects else ops.create_test_objects(tenant, prefix, departed["id"])
    print("No test objects (--no-objects): every 'Owns any …?' check should skip its loop." if a.no_objects else
          "Created (owned by the departed identity): " + ", ".join(o["name"] for o in made.values()))
    passed, approvals, campaign, cert = False, {}, None, None
    try:
        if requestee and cfg.pending_approvals:
            approvals = ops.make_test_approvals(tenant, made, departed["id"], requestee["id"],
                                                timeout=a.pending_timeout)
            print(f"Pending for {departed['name']}: the access profile's owner approval (direct) and the role's "
                  "governance-group approval (must stay with the group)")
        if requestee and cfg.pending_certifications:
            try:
                campaign, cert = ops.make_test_certification(tenant, prefix, departed["id"], requestee["id"],
                                                             timeout=a.pending_timeout)
            except TenantError as exc:
                campaign = (exc.body or {}).get("campaign") if isinstance(exc.body, dict) else None
                raise
            print(f"Open for {departed['name']}: the certification {cert['name']!r}")
        ex = ops.run_test(tenant, existing["id"], wf.test_input(departed["id"], departed["name"],
                                                                cfg.termination_states[0]), timeout=a.timeout)
        print(f"Execution {ex['id']}: {ex.get('status')}")
        owners = {k: ops.owner_of(tenant, wf.OBJECT_TYPES[k], o["id"]) for k, o in made.items()}
        checks = []
        for k, o in made.items():
            should = departed["id"] if (expect_failure or k not in cfg.object_types) else want_id
            checks.append((owners[k] == should, f"{o['name']}: owner {owners[k]} (expected {should})"))
        if approvals:
            direct = ops.approval_assignees(tenant, approvals["direct"]["id"])
            should = departed["id"] if expect_failure else want_id
            checks.append((direct == [should], f"direct approval: assigned to {direct} (expected [{should}])"))
            group = ops.approval_assignees(tenant, approvals["group"]["id"])
            checks.append((group == [made["governanceGroups"]["id"]],
                           f"group approval: assigned to {group} (expected the test governance group)"))
        if cert:
            reviewer = ops.certification_reviewer(tenant, cert["id"])
            should = departed["id"] if expect_failure else want_id
            checks.append((reviewer == should, f"certification: reviewer {reviewer} (expected {should})"))
        status_ok = ex.get("status") == ("Failed" if expect_failure else "Completed")
        checks.append((status_ok, f"run status {ex.get('status')} (expected {'Failed' if expect_failure else 'Completed'})"))
        for good, line in checks:
            print(("  ok    " if good else "  FAIL  ") + line)
        passed = all(good for good, _ in checks)
        if not passed or a.history:
            print("History:")
            for line in ops.history_summary(ops.history(tenant, ex["id"])):
                print("  " + line)
    finally:
        for approval in approvals.values():
            try:
                ops.cancel_test_request(tenant, approval)
            except TenantError as exc:
                print(f"could not cancel the test request behind approval {approval['id']}: {exc}")
        if campaign:
            try:
                ops.delete_test_campaign(tenant, campaign, prefix)
            except TenantError as exc:
                print(f"could not delete the test campaign: {exc}")
        if a.keep:
            print("Kept the test objects (--keep); the next test run removes them.")
        else:
            problems = ops.delete_test_objects(tenant, made, prefix)
            if made:
                print("Test objects deleted." if not problems else "\n".join(problems))
    print("PASS" if passed else "FAIL")
    return 0 if passed else 1


# ── status / uninstall ──────────────────────────────────────────────────────
def cmd_status(cfg: config_mod.Config, _a: argparse.Namespace) -> int:
    tenant = _tenant(cfg)
    existing = ops.find_workflow(tenant, cfg.workflow_name)
    if not existing:
        print(f"Workflow {cfg.workflow_name!r}: not installed in {tenant.tenant_name}.")
        return 1
    trig = (existing.get("trigger") or {}).get("attributes") or {}
    print(f"Workflow {existing['name']} ({existing['id']}) in {tenant.tenant_name}")
    print(f"  enabled: {existing.get('enabled')} · owner: {(existing.get('owner') or {}).get('name')}")
    print(f"  trigger: {trig.get('id')} {trig.get('filter.$')}")
    runs = tenant.call("GET", f"{ops.WORKFLOWS}/{existing['id']}/executions?limit=5") or []
    print("  recent runs:" if runs else "  no runs yet")
    for r in runs:
        print(f"    {r.get('startTime')}  {r.get('status'):<10} {r.get('id')}")
    return 0


def cmd_uninstall(cfg: config_mod.Config, a: argparse.Namespace) -> int:
    tenant = _tenant(cfg)
    existing = ops.find_workflow(tenant, cfg.workflow_name)
    if not existing:
        print(f"Workflow {cfg.workflow_name!r}: not installed; nothing to do.")
        return 0
    if not a.yes:
        answer = input(f"Delete workflow {existing['name']} ({existing['id']}) from {tenant.tenant_name}? [y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            print("Nothing deleted.")
            return 1
    if existing.get("enabled"):
        ops.set_enabled(tenant, existing["id"], False)
    tenant.call("DELETE", f"{ops.WORKFLOWS}/{existing['id']}")
    print(f"Deleted workflow {existing['id']}. Ownership changes it made stay as they are.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    def add(name: str, help_: str, config_required: bool = True) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_)
        p.add_argument("--config", required=config_required, help="config/<tenant>.json")
        return p

    add("doctor", "read-only preflight checks against the tenant")
    p = add("export", "write the workflow JSON with placeholders (for import without Python)", config_required=False)
    p.add_argument("--out", help=f"output file, or - for stdout (default {EXPORT_PATH.relative_to(Path(__file__).parent)})")
    p = add("apply", "create or update the workflow (disabled unless --enable)")
    p.add_argument("--dry-run", action="store_true", help="print the workflow body; change nothing")
    p.add_argument("--enable", action="store_true", help="enable the workflow after creating or updating it")
    p = add("test", "run the disabled workflow on throwaway objects owned by a test identity")
    p.add_argument("--departed", required=True, help="test identity (ID, username or email) that owns nothing")
    p.add_argument("--keep", action="store_true", help="keep the test objects afterwards")
    p.add_argument("--no-objects", action="store_true", help="create no test objects: checks the run when "
                                                             "the departed identity owns nothing")
    p.add_argument("--history", action="store_true", help="print the execution history even when the test passes")
    p.add_argument("--timeout", type=float, default=240, help="seconds to wait for the run (default 240)")
    p.add_argument("--pending-for", "--approval-for", dest="pending_for",
                   help="test identity (ID, username or email) to request the test items for and to review in a test "
                        "certification, so the departed identity has real pending work (takes minutes)")
    p.add_argument("--pending-timeout", "--approval-timeout", dest="pending_timeout", type=float, default=1200,
                   help="seconds to wait for SailPoint to create the test approvals and certification (default 1200)")
    add("status", "show the workflow and its recent runs")
    p = add("uninstall", "delete the workflow")
    p.add_argument("--yes", action="store_true", help="don't ask for confirmation")

    a = ap.parse_args(argv)
    try:
        cfg = config_mod.load(a.config) if a.config else None
        handler: Any = {"doctor": cmd_doctor, "export": cmd_export, "apply": cmd_apply, "test": cmd_test,
                        "status": cmd_status, "uninstall": cmd_uninstall}[a.command]
        return handler(cfg, a)
    except (config_mod.ConfigError, TenantError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
