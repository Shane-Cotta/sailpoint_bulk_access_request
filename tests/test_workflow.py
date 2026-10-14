"""Offline tests: the workflow graph, its ISC constraints, the config loader and the committed export.
No tenant needed: python -m pytest -q -p no:cacheprovider tests"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from reassign import config as config_mod  # noqa: E402
from reassign import workflow as wf  # noqa: E402

EXPORT = ROOT / "workflow" / "reassign-ownership-on-termination.json"


def cfg(**overrides: Any) -> config_mod.Config:
    return config_mod.from_dict({"prefix": "ACME", "fallbackOwner": "jane.doe", **overrides})


def only(*keys: str) -> dict[str, bool]:
    return {k: k in keys for k in config_mod.OBJECT_TYPE_KEYS}


VARIANTS = {
    "default": {},
    "fallback-only": {"ownerStrategy": "fallback-only"},
    "roles-only": {"objectTypes": only("roles")},
    "original-three": {"objectTypes": only("roles", "accessProfiles", "sources")},
    "listed-here-only": {"objectTypes": only("governanceGroups", "identityProfiles")},
    "apps-only": {"objectTypes": only("apps")},
    "no-sources": {"objectTypes": {"sources": False}},
    "several-states": {"terminationStates": ["terminated", "leaver", "inactive"]},
    "no-inactive-check": {"inactiveStates": []},
    "alerts": {"alertEmails": ["iam-team@example.com"]},
    "no-approvals": {"pendingApprovals": False},
    "approvals-roles-only": {"objectTypes": only("roles"), "pendingApprovals": True},
    "no-certifications": {"pendingCertifications": False},
    "certifications-only": {"pendingApprovals": False, "pendingCertifications": True},
    "no-pending-work": {"pendingApprovals": False, "pendingCertifications": False},
}


def all_steps(steps: dict[str, Any], in_loop: bool = False):
    """(name, step, in_loop) for every step, loop bodies included."""
    for name, step in steps.items():
        yield name, step, in_loop
        if step.get("actionId") == "sp:loop:iterator":
            yield from all_steps(step["attributes"]["steps"], True)


def targets(step: dict[str, Any]) -> list[str]:
    out = [step.get("nextStep"), step.get("defaultStep")]
    out += [c.get("nextStep") for c in step.get("choiceList") or []]
    return [t for t in out if t]


def json_paths(value: Any) -> list[str]:
    """Every JSONPath the step reads: `….$` values and `{{…}}` templates."""
    found: list[str] = []
    if isinstance(value, dict):
        for k, v in value.items():
            if k.endswith(".$") and isinstance(v, str):
                found.append(v)
            found += json_paths(v)
    elif isinstance(value, list):
        for v in value:
            found += json_paths(v)
    elif isinstance(value, str):
        found += re.findall(r"\{\{(.*?)\}\}", value)
    return found


@pytest.fixture(params=sorted(VARIANTS))
def built(request):
    c = cfg(**VARIANTS[request.param])
    return c, wf.build(c)


# ── graph ───────────────────────────────────────────────────────────────────
def test_every_step_target_exists(built):
    _, body = built
    top = body["definition"]["steps"]
    assert body["definition"]["start"] in top
    for name, step in top.items():
        for t in targets(step):
            assert t in top, f"{name} → {t} (missing)"
    for name, step, _ in all_steps(top):
        if step.get("actionId") == "sp:loop:iterator":
            inner = step["attributes"]["steps"]
            assert step["attributes"]["start"] in inner
            for n, s in inner.items():
                for t in targets(s):
                    assert t in inner, f"{name}/{n} → {t} (missing inside the loop)"


def test_every_step_is_reachable(built):
    _, body = built
    top = body["definition"]["steps"]
    seen, todo = set(), [body["definition"]["start"]]
    while todo:
        n = todo.pop()
        if n not in seen:
            seen.add(n)
            todo += targets(top[n])
    assert seen == set(top), f"unreachable: {set(top) - seen}"


def test_end_steps(built):
    _, body = built
    for name, step, _ in all_steps(body["definition"]["steps"]):
        if step.get("type") == "failure":
            assert step["failureName"] and step["description"], name     # validator error e300 otherwise
        if step.get("type") in ("success", "failure"):
            assert not targets(step), name


def test_loops_get_context_and_are_never_nested(built):
    _, body = built
    loops = [(n, s, inside) for n, s, inside in all_steps(body["definition"]["steps"])
             if s.get("actionId") == "sp:loop:iterator"]
    assert loops
    for name, step, inside in loops:
        assert not inside, f"{name} is nested in a loop (ISC rejects nested loops)"
        assert step["attributes"]["context.$"] == "$", name


def test_steps_in_loops_only_read_loop_paths(built):
    _, body = built
    for name, step, inside in all_steps(body["definition"]["steps"]):
        if inside:
            for path in json_paths(step):
                assert path.startswith("$.loop."), f"{name} reads {path}; inside a loop only $.loop.* exists"


def test_paths_read_existing_step_outputs(built):
    """Every $.<step>.… a step reads is the output of a step in the workflow (ISC names outputs by camelCase)."""
    c, body = built
    top = body["definition"]["steps"]
    outputs = {wf._camel(n) for n in top} | {"trigger", "loop"}
    for name, step, _ in all_steps(top):
        for path in json_paths(step):
            if path == "$":          # a loop's context: the whole run
                continue
            root = path.split(".")[1].split("[")[0]
            if root == "loop":
                rest = path.split(".")
                if len(rest) > 3 and rest[2] == "context":
                    assert rest[3] in outputs, f"{name} reads {path}"
                continue
            assert root in outputs, f"{name} reads {path}"


def test_variable_steps(built):
    _, body = built
    top = body["definition"]["steps"]
    assert wf.DEFINE_OWNER.startswith("Define Variable")    # Update Variable only accepts these
    for name, step in top.items():
        if (step.get("attributes") or {}).get("id") == "sp:update-variable":
            for v in step["attributes"]["variables"]:
                assert v["name"].startswith(wf.OWNER_VARS + "."), name


def test_http_steps(built):
    c, body = built
    found = []
    for name, step, _ in all_steps(body["definition"]["steps"]):
        if step.get("actionId") != "sp:http":
            continue
        found.append(name)
        a = step["attributes"]
        assert step["versionNumber"] == 3
        assert a["param_authenticationRef"] == "oauth"
        assert a["param_oauth"]["paramType"] == wf.OAUTH_PARAMETER_TYPE and a["param_oauth"]["refID"]
        assert not any("ecret" in k for k in a), f"{name} carries a secret"
        if name in (wf.GET_APPROVALS, wf.RECHECK_APPROVALS, wf.REASSIGN_APPROVAL):
            assert a.get("requestHeaders") is None and a["requestContentType"] == "json"
            if name == wf.REASSIGN_APPROVAL:
                assert a["method"] == "post"
                assert a["url"] == f"{wf.API_URL}/generic-approvals/v1/{{{{$.loop.loopInput.id}}}}/reassign"
            else:
                assert a["method"] == "get" and a["url"] == f"{wf.API_URL}/generic-approvals/v1"
            continue
        if name in (wf.GET_CERTS, wf.RECHECK_CERTS, wf.REASSIGN_CERT):
            assert a.get("requestHeaders") is None and a["requestContentType"] == "json"
            if name == wf.REASSIGN_CERT:
                assert a["method"] == "post"
                assert a["url"] == f"{wf.API_URL}/campaigns/v1/{{{{$.loop.loopInput.campaign.id}}}}/reassign"
            else:
                assert a["method"] == "get" and a["url"] == f"{wf.API_URL}/certifications/v1"
            continue
        assert a["method"] in ("get", "patch")
        ot = next(o for o in wf.OBJECT_TYPES.values() if o.plural in name or o.singular in name)
        assert a.get("requestHeaders") == ot.headers
        if a["method"] == "patch":
            assert a["url"] == f"{wf.API_URL}{ot.item_path}/{{{{$.loop.loopInput.id}}}}"
            assert a["requestContentType"] == "json-patch+json"
            assert a["jsonPatchRequestBody"][0]["path"] == "/owner"
            assert "name" not in a["jsonPatchRequestBody"][0]["value"]    # a stale name is a 400
        else:
            assert a["url"] == f"{wf.API_URL}{ot.path}"
            if ot.server_filter:
                assert 'filters:owner.id eq "{{$.trigger.identity.id}}"' in a["urlParams"]
            else:
                assert a["urlParams"] == f"limit:{wf.PAGE}"           # these lists reject an owner filter
    assert sorted(found) == sorted(wf.http_step_names(c))


def test_ref_ids_round_trip(built):
    c, body = built
    refs = wf.ref_ids_of(body)
    assert set(refs) == set(wf.http_step_names(c)) and len(set(refs.values())) == len(refs)
    again = wf.build(c, wf.Target(ref_ids=refs))
    assert wf.ref_ids_of(again) == refs


# ── behaviour per config ────────────────────────────────────────────────────
def test_trigger_filter_lists_every_state():
    c = cfg(terminationStates=["terminated", "leaver"])
    trig = wf.build(c)["trigger"]
    assert trig == {"type": "EVENT", "attributes": {
        "id": "idn:identity-lifecycle-state-changed",
        "filter.$": '$[?(@.newLifecycleState == "terminated" || @.newLifecycleState == "leaver")]'}}


def test_fallback_only_has_no_manager_steps():
    top = wf.build(cfg(ownerStrategy="fallback-only"))["definition"]["steps"]
    assert wf.HAS_MANAGER not in top and wf.GET_MANAGER not in top
    assert top[wf.DEFINE_OWNER]["nextStep"] == wf.OWNER_USABLE


def test_manager_inactive_regex():
    top = wf.build(cfg(inactiveStates=["terminated", "archived"]))["definition"]["steps"]
    entry = top[wf.MANAGER_INACTIVE]["choiceList"][0]
    assert entry["variableB"] == "^(?:terminated|archived)$" and entry["nextStep"] == wf.OWNER_USABLE
    assert top[wf.MANAGER_INACTIVE]["defaultStep"] == wf.USE_MANAGER
    assert wf.MANAGER_INACTIVE not in wf.build(cfg(inactiveStates=[]))["definition"]["steps"]


def test_owner_usable_guards():
    entries = wf.build(cfg())["definition"]["steps"][wf.OWNER_USABLE]["choiceList"]
    assert {"comparator": "StringEquals", "variableA.$": wf.NEW_OWNER_ID, "variableB.$": "$.trigger.identity.id",
            "nextStep": wf.STOP_NO_OWNER} in entries
    assert any(e.get("variableB") == "^__" for e in entries)        # an unfilled placeholder never patches


def test_new_owner_is_checked_before_any_change():
    top = wf.build(cfg())["definition"]["steps"]
    assert top[wf.OWNER_USABLE]["defaultStep"] == wf.GET_NEW_OWNER
    assert top[wf.GET_NEW_OWNER]["attributes"] == {"id.$": wf.NEW_OWNER_ID}
    found = top[wf.NEW_OWNER_FOUND]
    assert found["choiceList"][0]["variableA.$"] == "$.getNewOwner.id" and found["defaultStep"] == wf.STOP_NO_OWNER
    active = top[wf.NEW_OWNER_ACTIVE]
    assert active["choiceList"][0]["nextStep"] == wf.STOP_NO_OWNER
    assert active["defaultStep"] == wf.OBJECT_TYPES["roles"].step("Get Owned")
    no_check = wf.build(cfg(inactiveStates=[]))["definition"]["steps"]
    assert wf.NEW_OWNER_ACTIVE not in no_check
    assert no_check[wf.NEW_OWNER_FOUND]["choiceList"][0]["nextStep"] == wf.OBJECT_TYPES["roles"].step("Get Owned")


def test_types_filtered_in_the_workflow():
    """Governance groups and identity profiles: every object is listed, theirs are picked out with JSONPath in the
    loop's input, in Owns? and in Anything Left?; their Recheck lists a full page, not one row."""
    top = wf.build(cfg())["definition"]["steps"]
    for key in ("governanceGroups", "identityProfiles"):
        ot = wf.OBJECT_TYPES[key]
        mine = f"{ot.output('Get Owned')}.body[?(@.owner.id == $.trigger.identity.id)]"
        assert top[ot.step("Reassign")]["attributes"]["input.$"] == mine
        assert top[ot.step("Owns")]["choiceList"][0]["variableA.$"] == mine + ".id"
        assert top[ot.step("Recheck")]["attributes"]["urlParams"] == f"limit:{wf.PAGE}"
        left = [e["variableA.$"] for e in top[wf.ANYTHING_LEFT]["choiceList"]]
        assert f"{ot.output('Recheck')}.body[?(@.owner.id == $.trigger.identity.id)].id" in left
    for key in ("roles", "accessProfiles", "entitlements", "sources", "apps"):
        ot = wf.OBJECT_TYPES[key]
        assert top[ot.step("Reassign")]["attributes"]["input.$"] == f"{ot.output('Get Owned')}.body"
        assert top[ot.step("Recheck")]["attributes"]["urlParams"].endswith("limit:1")


def test_apps_use_the_admin_list_and_the_experimental_header():
    top = wf.build(cfg(objectTypes=only("apps")))["definition"]["steps"]
    get = top["Get Owned Apps"]["attributes"]
    patch = top["Reassign Apps"]["attributes"]["steps"]["Patch App Owner"]["attributes"]
    assert get["url"].endswith("/source-apps/v1/all")              # /source-apps lists only the caller's apps
    assert patch["url"].endswith("/source-apps/v1/{{$.loop.loopInput.id}}")
    assert get["requestHeaders"] == patch["requestHeaders"] == "X-SailPoint-Experimental:true"


def test_every_type_is_supported_by_default():
    c = config_mod.from_dict({"fallbackOwner": "x"})
    assert c.object_types == config_mod.OBJECT_TYPE_KEYS == tuple(wf.OBJECT_TYPES)
    # an older config that lists only the original three keys gets the new types too (missing keys are on)
    old = config_mod.from_dict({"fallbackOwner": "x", "objectTypes": {"roles": True, "accessProfiles": True,
                                                                      "sources": True}})
    assert old.object_types == config_mod.OBJECT_TYPE_KEYS


def test_disabled_types_are_left_out():
    top = wf.build(cfg(objectTypes={"sources": False}))["definition"]["steps"]
    assert not any("Source" in n for n in top)
    assert "sources" not in wf.build(cfg(objectTypes={"sources": False}))["description"]


def test_pending_approvals_are_reassigned_after_the_objects_and_rechecked():
    c = cfg(pendingCertifications=False)
    assert c.pending_approvals                                       # on by default
    steps = wf.build(c)["definition"]["steps"]
    last = wf.OBJECT_TYPES[c.object_types[-1]]
    first_recheck = wf.OBJECT_TYPES[c.object_types[0]].step("Recheck")
    # After the last object type (whether or not it owned any), the approvals stage; then the rechecks.
    assert steps[last.step("Owns")]["defaultStep"] == wf.GET_APPROVALS
    assert steps[last.step("Reassign")]["nextStep"] == wf.GET_APPROVALS
    get = steps[wf.GET_APPROVALS]["attributes"]
    assert get["urlParams"] == 'approverId:{{$.trigger.identity.id}}\nfilters:status eq "PENDING"\nlimit:250'
    has = steps[wf.HAS_APPROVALS]
    mine = "[?(@.assignedTo[0].identityID == $.trigger.identity.id)]"   # not a governance group's approvals
    assert has["choiceList"] == [{"comparator": "IsPresent", "variableA.$": f"$.getPendingApprovals.body{mine}.id",
                                  "nextStep": wf.REASSIGN_APPROVALS}]
    assert has["defaultStep"] == first_recheck
    loop = steps[wf.REASSIGN_APPROVALS]
    assert loop["attributes"]["input.$"] == f"$.getPendingApprovals.body{mine}" and loop["nextStep"] == first_recheck
    post = loop["attributes"]["steps"][wf.REASSIGN_APPROVAL]["attributes"]
    assert post["jsonRequestBody"]["reassignTo"] == "{{$.loop.context.defineVariableOwner.newOwnerId}}"
    assert "{{" not in post["jsonRequestBody"]["comment"]               # fixed text: nothing spliced in raw
    assert "ACME Reassign Ownership on Termination" in post["jsonRequestBody"]["comment"]
    # The last object recheck goes to the approvals recheck, which feeds Anything Left?.
    assert steps[last.step("Recheck")]["nextStep"] == wf.RECHECK_APPROVALS
    assert steps[wf.RECHECK_APPROVALS]["attributes"]["urlParams"].endswith("limit:250")
    assert steps[wf.RECHECK_APPROVALS]["nextStep"] == wf.ANYTHING_LEFT
    assert {"comparator": "IsPresent", "variableA.$": f"$.recheckPendingApprovals.body{mine}.id",
            "nextStep": wf.STOP_LEFTOVERS} in steps[wf.ANYTHING_LEFT]["choiceList"]
    assert "pending approvals" in wf.description(c)


def test_open_certifications_are_reassigned_whole_after_the_approvals():
    c = cfg()
    assert c.pending_certifications                                  # on by default
    steps = wf.build(c)["definition"]["steps"]
    first_recheck = wf.OBJECT_TYPES[c.object_types[0]].step("Recheck")
    # Objects → approvals → certifications → rechecks (objects, approvals, certifications) → Anything Left?
    assert steps[wf.HAS_APPROVALS]["defaultStep"] == wf.GET_CERTS
    assert steps[wf.REASSIGN_APPROVALS]["nextStep"] == wf.GET_CERTS
    get = steps[wf.GET_CERTS]["attributes"]
    assert get["urlParams"] == ('reviewer-identity:{{$.trigger.identity.id}}\n'
                                'filters:phase eq "ACTIVE" and completed eq false\nlimit:250')
    mine = "[?(@.reviewer.id == $.trigger.identity.id)]"
    has = steps[wf.HAS_CERTS]
    assert has["choiceList"] == [{"comparator": "IsPresent", "variableA.$": f"$.getOpenCertifications.body{mine}.id",
                                  "nextStep": wf.REASSIGN_CERTS}]
    assert has["defaultStep"] == first_recheck
    loop = steps[wf.REASSIGN_CERTS]
    assert loop["attributes"]["input.$"] == f"$.getOpenCertifications.body{mine}" and loop["nextStep"] == wf.WAIT_CERTS
    wait = steps[wf.WAIT_CERTS]                    # the campaign reassign is queued; let it land before the recheck
    assert (wait["actionId"], wait["attributes"]["type"], wait["nextStep"]) == ("sp:sleep", "waitFor", first_recheck)
    post = loop["attributes"]["steps"][wf.REASSIGN_CERT]["attributes"]
    assert post["jsonRequestBody"] == {
        "certificationIds": ["{{$.loop.loopInput.id}}"],
        "reassignTo": {"id": "{{$.loop.context.defineVariableOwner.newOwnerId}}", "type": "IDENTITY"},
        "reason": "Reassigned automatically: the reviewer has left (workflow ACME Reassign Ownership on Termination)."}
    assert steps[wf.RECHECK_APPROVALS]["nextStep"] == wf.RECHECK_CERTS
    assert steps[wf.RECHECK_CERTS]["nextStep"] == wf.ANYTHING_LEFT
    assert {"comparator": "IsPresent", "variableA.$": f"$.recheckOpenCertifications.body{mine}.id",
            "nextStep": wf.STOP_LEFTOVERS} in steps[wf.ANYTHING_LEFT]["choiceList"]
    assert "pending approvals and their open certifications" in wf.description(c)


def test_certifications_alone_follow_the_objects():
    c = cfg(pendingApprovals=False)
    steps = wf.build(c)["definition"]["steps"]
    last = wf.OBJECT_TYPES[c.object_types[-1]]
    assert steps[last.step("Owns")]["defaultStep"] == wf.GET_CERTS
    assert steps[last.step("Recheck")]["nextStep"] == wf.RECHECK_CERTS
    assert wf.GET_APPROVALS not in steps


def test_pending_approvals_can_be_turned_off():
    c = cfg(pendingApprovals=False, pendingCertifications=False)
    steps = wf.build(c)["definition"]["steps"]
    assert not {wf.GET_APPROVALS, wf.HAS_APPROVALS, wf.REASSIGN_APPROVALS, wf.RECHECK_APPROVALS} & set(steps)
    last = wf.OBJECT_TYPES[c.object_types[-1]]
    assert steps[last.step("Recheck")]["nextStep"] == wf.ANYTHING_LEFT
    assert "approval" not in wf.description(c)


def test_alert_email_only_when_configured():
    plain = wf.build(cfg())["definition"]["steps"]
    assert wf.ALERT_ADMINS not in plain
    assert all(e["nextStep"] == wf.STOP_LEFTOVERS for e in plain[wf.ANYTHING_LEFT]["choiceList"])
    top = wf.build(cfg(alertEmails=["iam-team@example.com"]))["definition"]["steps"]
    mail = top[wf.ALERT_ADMINS]
    assert mail["attributes"]["recipientEmailList"] == ["iam-team@example.com"]
    assert "{{" not in mail["attributes"]["body"]          # values only come in through context (Velocity safe)
    assert all(e["nextStep"] == wf.ALERT_ADMINS for e in top[wf.ANYTHING_LEFT]["choiceList"])


def test_workflow_is_created_disabled():
    assert wf.build(cfg())["enabled"] is False


# ── export ──────────────────────────────────────────────────────────────────
def test_committed_export_is_current():
    assert EXPORT.read_text() == wf.pretty(wf.build(config_mod.defaults())), \
        "workflow/*.json is stale: run python ownership.py export"


def test_export_has_no_tenant_specific_values():
    text = EXPORT.read_text()
    for ph in wf.PLACEHOLDERS:
        assert ph in text
    assert not re.search(r"https?://", text.replace(wf.API_URL, "")), "a real URL in the export"
    assert not re.search(r"\b[0-9a-f]{32}\b", text), "a tenant object ID in the export"
    body = json.loads(text)
    assert body["name"] == "Reassign Ownership on Termination"


# ── config ──────────────────────────────────────────────────────────────────
def test_example_config_loads():
    c = config_mod.load(ROOT / "config" / "ownership.example.json")
    assert c.prefix == "ACME" and c.object_types == config_mod.OBJECT_TYPE_KEYS and c.parameter


@pytest.mark.parametrize("bad, message", [
    ({"terminationStates": []}, "at least one"),
    ({"terminationStates": ["Terminated Employee"]}, "technical names"),
    ({"ownerStrategy": "random"}, "ownerStrategy"),
    ({"objectTypes": {"workflows": True}}, "unknown"),
    ({"objectTypes": {k: False for k in config_mod.OBJECT_TYPE_KEYS}}, "enable at least one"),
    ({"alertEmails": ["not-an-email"]}, "email"),
    ({"alertEmails": [f"a{i}@example.com" for i in range(11)]}, "at most 10"),
    ({"http": {"clientSecretEnv": "X"}}, "Parameter Storage"),
    ({"http": {"parameter": " "}}, "http.parameter"),
    ({"prefix": "-bad"}, "prefix"),
    ({"pendingApprovals": "yes"}, "pendingApprovals"),
    ({"pendingCertifications": 1}, "pendingCertifications"),
])
def test_config_errors(bad, message):
    with pytest.raises(config_mod.ConfigError, match=message):
        config_mod.from_dict({"fallbackOwner": "x", **bad})


def test_test_input_matches_trigger_shape():
    p = wf.test_input("id1", "jo", "terminated")
    assert p["identity"] == {"id": "id1", "name": "jo", "type": "IDENTITY"}
    assert p["newLifecycleState"] == "terminated" and "oldLifecycleState" in p


def test_governance_connector_settings_map_to_our_options():
    from reassign import ops
    ours = set(config_mod.OBJECT_TYPE_KEYS) | {"pendingApprovals", "pendingCertifications"}
    mapped = {v for v in ops.GOVERNANCE_REASSIGN_KEYS.values() if v}
    assert mapped <= ours
    assert ops.GOVERNANCE_REASSIGN_KEYS["reassignWorkflows"] is None        # the connector does workflows; we don't
    assert len(ops.GOVERNANCE_REASSIGN_KEYS) == 8                           # its eight checkboxes
