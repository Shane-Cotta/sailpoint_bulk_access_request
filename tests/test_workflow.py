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


VARIANTS = {
    "default": {},
    "fallback-only": {"ownerStrategy": "fallback-only"},
    "roles-only": {"objectTypes": {"roles": True, "accessProfiles": False, "sources": False}},
    "no-sources": {"objectTypes": {"sources": False}},
    "several-states": {"terminationStates": ["terminated", "leaver", "inactive"]},
    "no-inactive-check": {"inactiveStates": []},
    "alerts": {"alertEmails": ["iam-team@example.com"]},
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
        assert a["method"] in ("get", "patch")
        if a["method"] == "patch":
            assert a["requestContentType"] == "json-patch+json"
            assert a["jsonPatchRequestBody"][0]["path"] == "/owner"
            assert "name" not in a["jsonPatchRequestBody"][0]["value"]    # a stale name is a 400
        else:
            assert 'filters:owner.id eq "{{$.trigger.identity.id}}"' in a["urlParams"]
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


def test_disabled_types_are_left_out():
    top = wf.build(cfg(objectTypes={"sources": False}))["definition"]["steps"]
    assert not any("Source" in n for n in top)
    assert "sources" not in wf.build(cfg(objectTypes={"sources": False}))["description"]


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
    ({"objectTypes": {"entitlements": True}}, "unknown"),
    ({"objectTypes": {"roles": False, "accessProfiles": False, "sources": False}}, "enable at least one"),
    ({"alertEmails": ["not-an-email"]}, "email"),
    ({"alertEmails": [f"a{i}@example.com" for i in range(11)]}, "at most 10"),
    ({"http": {"clientSecretEnv": "X"}}, "Parameter Storage"),
    ({"http": {"parameter": " "}}, "http.parameter"),
    ({"prefix": "-bad"}, "prefix"),
])
def test_config_errors(bad, message):
    with pytest.raises(config_mod.ConfigError, match=message):
        config_mod.from_dict({"fallbackOwner": "x", **bad})


def test_test_input_matches_trigger_shape():
    p = wf.test_input("id1", "jo", "terminated")
    assert p["identity"] == {"id": "id1", "name": "jo", "type": "IDENTITY"}
    assert p["newLifecycleState"] == "terminated" and "oldLifecycleState" in p
