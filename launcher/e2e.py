#!/usr/bin/env python3
"""End-to-end test of the Launcher deployment, driven entirely through the API.

    python launcher/e2e.py --config config/<tenant>.json --people <identityId>,<identityId> \\
        [--items "<item name>,<item name>"] [--scenario request|bad-duration]
        [--access temporary --duration 1 --unit DAYS] [--part 2/3]

What it does, as the PAT user (who must be allowed to launch the Launcher):
  1. starts the Launcher                         (POST /launchers/v1/{id}/launch)
  2. fills in and submits the intake form        (PATCH /form-instances/v1/{id})
  3. waits for the workflow run and checks the steps it took: the requests are filed straight after the checks
     (there is no approval of the bulk request itself), and the requester is emailed;
  4. in live mode, checks every person got one access request per item with the INC, the requester and the access
     label in its comment (no "Approved by"), and (for temporary access) a removeDate of about now + the duration.
     Each request then goes through the item's own approval, as SailPoint has it set up.

--part k/n adds `partLabel` " (k/n)" to the form data (the form has no field for it), as the UI plugin does when it submits a big request
through the Launcher in parts. Without it the field is left out, as the Launchpad does.

Scenarios: request (default), and bad-duration (temporary access with an invalid duration), which must be stopped
before anything is requested.

--items picks items on the form by name (default: every item on the form). In live mode it is required, so a run
only ever requests the test items it names.

Use only test identities and test items: runs really request access when the config's mode is "live". Every run
uses a fresh random INC number.
"""

from __future__ import annotations

import argparse
import calendar
import random
import sys
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from bulkaccess import config as config_mod  # noqa: E402
from bulkaccess import api, definitions, rules  # noqa: E402
from bulkaccess.config import DURATION_UNITS  # noqa: E402
from bulkaccess.tenant import Tenant, TenantError  # noqa: E402
import install  # noqa: E402  (reuse its find_* helpers)


def wait(fn, what: str, timeout: float = 120, every: float = 3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(every)
    raise SystemExit(f"FAIL: timed out waiting for {what}")


def add_duration(start: datetime, n: int, unit: str) -> datetime:
    """start + n units, the way Manage Access computes removeDate (months are calendar months)."""
    if unit == "HOURS":
        return start + timedelta(hours=n)
    if unit == "DAYS":
        return start + timedelta(days=n)
    if unit == "WEEKS":
        return start + timedelta(weeks=n)
    month = start.month - 1 + n
    year, month = start.year + month // 12, month % 12 + 1
    return start.replace(year=year, month=month, day=min(start.day, calendar.monthrange(year, month)[1]))


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def shapes(data: dict, keys) -> str:
    return ", ".join(f"{k}={type(data.get(k)).__name__}:{data.get(k)!r}" for k in keys if k in data)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True)
    ap.add_argument("--people", required=True, help="comma-separated identity IDs (test identities only)")
    ap.add_argument("--items", default="", help="comma-separated item names on the form (test items only); "
                    "required in live mode. Default: every item on the form")
    ap.add_argument("--justification", default="", help="override the justification text (e.g. to test long input)")
    ap.add_argument("--scenario", choices=["request", "bad-duration"], default="request",
                    help="bad-duration = temporary access with an invalid duration (default: none entered); it must be "
                         "stopped before anything is requested")
    ap.add_argument("--access", choices=["permanent", "temporary"], default="permanent")
    ap.add_argument("--duration", default="1", help="with --access temporary: the duration (sent as the form's text)")
    ap.add_argument("--unit", choices=list(DURATION_UNITS), default="DAYS", help="with --access temporary")
    ap.add_argument("--part", default="", help="k/n: send the part label \" (k/n)\" like the plugin does")
    a = ap.parse_args(argv)
    if a.part:
        k, _, n = a.part.partition("/")
        if not (k.isdigit() and n.isdigit() and 1 <= int(k) <= int(n)):
            raise SystemExit("--part must look like 2/3")
    if a.scenario == "bad-duration":
        a.access = "temporary"
        if a.duration == "1":
            # Temporary with no duration: passes the form (the field is optional while hidden)
            # and must be stopped by the workflow. "0" or "abc" is refused by the form itself.
            a.duration = ""

    cfg = config_mod.load(a.config)
    t = Tenant.from_env(cfg.env_file)
    people = [p.strip() for p in a.people.split(",") if p.strip()]
    inc = f"INC{random.randint(0, 9_999_999):07d}"
    part = rules.part_label(int(a.part.split("/")[0]), int(a.part.split("/")[1])) if a.part else ""
    temporary = a.access == "temporary"
    expected_label = f"Temporary: {a.duration}{DURATION_UNITS[a.unit]}" if temporary else "Permanent"
    print(f"Tenant {t.tenant_name} · mode {cfg.mode} · scenario {a.scenario} · {inc}{part} · {len(people)} people · "
          f"access {expected_label}")

    form = install.find_form(t, cfg.form_name)
    wf = install.find_workflow(t, cfg.launcher_workflow_name)
    launcher = install.find_launcher(t, cfg.launcher_name)
    if not (form and wf and launcher):
        raise SystemExit("FAIL: not installed. Run launcher/install.py first.")
    items = install.catalog_items(form)
    if a.items:
        wanted = [n.strip() for n in a.items.split(",") if n.strip()]
        missing = [n for n in wanted if n not in {i["name"] for i in items}]
        if missing:
            raise SystemExit(f"FAIL: not on the form: {missing}")
        items = [i for i in items if i["name"] in wanted]
    elif cfg.mode == "live" and a.scenario == "request":
        raise SystemExit("FAIL: mode is live, so name the test items to request with --items (the form offers "
                         f"{len(items)}).")
    print(f"Items: {[i['name'] for i in items]}")
    has_temporary = install.form_element(form, definitions.F_ACCESS_TYPE) is not None
    if temporary and not has_temporary:
        raise SystemExit("FAIL: the installed form has no temporary access fields (temporaryAccess is off for the Launcher).")

    # 1. Launch
    # 20 s of slack for clock differences between this machine and the tenant.
    launched_at = (datetime.now(timezone.utc) - timedelta(seconds=20)).strftime("%Y-%m-%dT%H:%M:%S")
    started = t.call("POST", f"{api.LAUNCHERS}/{launcher['id']}/launch", {})
    ipid = started.get("interactiveProcessId")
    print(f"1. Launched: interactive process {ipid}")

    # 2. Find the form instance assigned to us and submit it
    def instance():
        # Filtered to our form, at the list's maximum: the list returns oldest first and ignores offset, so with
        # limit=50 a tenant with more forms than that never showed the new one (seen live).
        flt = urllib.parse.quote(f'formDefinitionId eq "{form["id"]}"')
        res = t.call("GET", f"{api.FORM_INSTANCES}?limit=250&filters={flt}") or {}
        rows = res.get("results") if isinstance(res, dict) else res
        # Only a form created by *this* launch: an older open form (e.g. one a person left
        # half-filled in the Launchpad) must never be picked up and submitted.
        mine = [r for r in rows or [] if r.get("formDefinitionId") == form["id"]
                and r.get("state") in ("ASSIGNED", "IN_PROGRESS") and r.get("created", "")[:19] >= launched_at]
        mine.sort(key=lambda r: r.get("created", ""), reverse=True)
        return mine[0] if mine else None
    inst = wait(instance, "the form to be assigned")
    form_data = {"people": people, "items": items, "inc": inc,
                 "justification": a.justification or f"E2E test {inc} ({a.scenario}) by launcher/e2e.py"}
    if part:
        form_data[definitions.F_PART_LABEL] = part
    if has_temporary:
        # The shapes the Launchpad sends: a TOGGLE is a boolean, a TEXT a string, and a SELECT a list
        # (the workflow engine unwraps one-item lists, so a scalar works too; verified live).
        # (rules.launcher_form_access builds the same fields from a removeDuration; the duration is sent raw here
        # so the bad-duration scenario can send what the form would refuse.)
        form_data.update({definitions.F_ACCESS_TYPE: temporary,
                          definitions.F_DURATION: a.duration if temporary else "",
                          definitions.F_DURATION_UNIT: [DURATION_UNITS[a.unit]] if temporary else []})
    # The run this launch started (the form instance names it), so no other run is ever picked up.
    run_id = (inst.get("createdBy") or {}).get("id")

    # A form moves ASSIGNED -> IN_PROGRESS -> SUBMITTED; one PATCH may only advance it one
    # step, so repeat until it is SUBMITTED. The API applies the form's validations: a value
    # they refuse leaves the form IN_PROGRESS with `formErrors` (the workflow never continues).
    for _ in range(3):
        t.call("PATCH", f"{api.FORM_INSTANCES}/{inst['id']}", [
            {"op": "replace", "path": "/formData", "value": form_data},
            {"op": "replace", "path": "/state", "value": "SUBMITTED"},
        ], content_type="application/json-patch+json")
        current = t.call("GET", f"{api.FORM_INSTANCES}/{inst['id']}") or {}
        if current.get("state") in ("SUBMITTED", "COMPLETED") or current.get("formErrors"):
            break
    stored = current.get("formData") or {}
    if has_temporary:
        print(f"   formData as stored: {shapes(stored, (definitions.F_ACCESS_TYPE, definitions.F_DURATION, definitions.F_DURATION_UNIT))}")
    if current.get("state") not in ("SUBMITTED", "COMPLETED"):   # COMPLETED: the workflow already took it
        errors = {e.get("key"): [m.get("text") for m in e.get("messages") or []] for e in current.get("formErrors") or []}
        if run_id:   # don't leave the run waiting for a form nobody will submit
            t.call("POST", f"{api.WORKFLOW_EXECUTIONS}/{run_id}/cancel")
        if a.scenario == "bad-duration" and set(errors) == {definitions.F_DURATION}:
            print(f"2. Form {inst['id']} refused by the form's own validation: {errors}; run {run_id} cancelled")
            print("PASS (stopped by the form, before any workflow step)")
            return 0
        raise SystemExit(f"FAIL: form {inst['id']} would not submit (state {current.get('state')}, errors {errors}); "
                         f"run {run_id} cancelled")
    print(f"2. Form {inst['id']} submitted (run {run_id})")

    submitted_at = datetime.now(timezone.utc)

    def run_done():
        run = t.call("GET", f"{api.WORKFLOW_EXECUTIONS}/{run_id}") or {}
        return run if run.get("status") in ("Completed", "Failed", "Canceled") else None

    run = wait(run_done, "the workflow run to finish", timeout=240)
    history = (t.call("GET", f"{api.WORKFLOW_EXECUTIONS}/{run['id']}/history-v2") or {}).get("history") or []
    done = [h["attributes"].get("stepName") for h in history if h.get("type") == "ActivityTaskCompleted"]
    print(f"3. Workflow run {run['id']}: {run['status']}; steps completed: {done}")
    if any("approv" in (s or "").lower() for s in done):
        print("FAIL: the run took an approval step; bulk requests have none")
        return 1

    if a.scenario == "bad-duration":
        stops = {"rejectBadDuration", "rejectBadUnit", "rejectTooLong"}
        # The Launchpad message, then the email naming the field to fix (a failed send fails the run).
        emailed = {f"email{s[0].upper()}{s[1:]}" for s in stops} & set(done)
        ok = bool(stops & set(done)) and bool(emailed) and "requestAccess" not in done and "notifyRequested" not in done
        print(f"   rejection email sent: {sorted(emailed) or 'NO'}")
        print("PASS" if ok else "FAIL")
        return 0 if ok else 1

    ok = run["status"] == "Completed" and {"notifyRequested", "emailRequested"} <= set(done)
    print(f"   emails sent: {[s for s in done if s.startswith('email')]}")
    if cfg.live:
        ok &= "requestAccess" in done
        for person in people:
            def carrying():   # the status API lags the request by a few seconds
                rows = t.call("GET", f"{api.ACCESS_REQUEST_STATUS}?requested-for={person}&limit=20") or []
                found = [r for r in rows if inc in ((r.get("requesterComment") or {}).get("comment") or "")]
                return found if len(found) >= len(items) else None
            try:
                hits = wait(carrying, f"requests for {person}", timeout=90)
            except SystemExit:
                hits = []
            print(f"   {person}: {len(hits)} request(s) carrying {inc}: {[(r.get('name'), r.get('state')) for r in hits]}")
            ok &= len(hits) == len(items)
            for r in hits:
                comment = (r.get("requesterComment") or {}).get("comment") or ""
                remove = parse_time(r.get("removeDate"))
                if temporary:
                    want = add_duration(submitted_at, int(a.duration), a.unit)
                    good = remove is not None and abs((remove - want).total_seconds()) <= 600
                    print(f"     removeDate {r.get('removeDate')} (expected about {want:%Y-%m-%dT%H:%M}Z): {'ok' if good else 'WRONG'}")
                else:
                    good = remove is None
                    print(f"     removeDate {r.get('removeDate')} (expected none): {'ok' if good else 'WRONG'}")
                parsed = rules.parse_bulk_comment(cfg, comment)
                good_comment = bool(parsed) and parsed["accessLabel"] == expected_label and parsed["approver"] is None
                print(f"     comment {comment!r}: {'ok' if good_comment else 'WRONG'}")
                ok &= good and good_comment
    else:
        ok &= "requestAccess" not in done and "manageAccess" not in done
        print("   dry-run: confirmed nothing was requested")
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (config_mod.ConfigError, TenantError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
