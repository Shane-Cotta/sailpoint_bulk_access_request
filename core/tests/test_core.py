"""Core unit tests: config validation, rules, and the generated definitions. No tenant needed."""

import importlib.util
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from bulkaccess import catalog, config, definitions, rules

EXAMPLE = Path(__file__).resolve().parents[2] / "config" / "bulk-access.example.json"


def cfg_with(**overrides):
    data = json.loads(EXAMPLE.read_text())
    for dotted, value in overrides.items():
        target = data
        *parents, leaf = dotted.split("__")
        for part in parents:
            target = target.setdefault(part, {})
        target[leaf] = value
    return config.from_dict(data)


# ── config ────────────────────────────────────────────────────────────────────
def test_example_config_loads_in_dry_run():
    cfg = config.load(EXAMPLE)
    assert cfg.prefix == "ACME" and cfg.mode == "dry-run" and not cfg.live
    assert cfg.form_name == "ACME Bulk Access Request Form"
    assert cfg.old_plugin_workflow_name == "ACME Bulk Access Request (Plugin)"
    assert cfg.deprecations == ()


@pytest.mark.parametrize("key,value,fragment", [
    ("prefix", "", "prefix"),
    ("mode", "yolo", "mode"),
    ("inc__pattern", "([", "regular expression"),
    ("inc__example", "CHG123", "does not match"),
    ("catalog__types", ["GROUP"], "catalog.types"),
    ("catalog__maxItems", 26, "maxItems"),
    ("notifications__overrideRecipients", ["not-an-email"], "email"),
    ("plugin__alias", "Has Spaces", "alias"),
])
def test_bad_config_is_rejected_with_a_useful_message(key, value, fragment):
    with pytest.raises(config.ConfigError, match=fragment):
        cfg_with(**{key: value})


def test_bulk_approval_settings_still_load_but_are_ignored_with_a_note():
    # Configs written while bulk requests had one approver keep working (and say what to remove).
    data = json.loads(EXAMPLE.read_text())
    data["approval"] = {"timeoutDays": 0, "actionAtTimeout": "nonsense", "priority": "HIGH"}
    data["notifications"].update(ccApprover=True, pendingEmail="yes")
    cfg = config.from_dict(data)
    assert [d.split("`")[1] for d in cfg.deprecations] == ["approval", "notifications.ccApprover",
                                                            "notifications.pendingEmail"]
    assert all(config.REMOVED_BULK_APPROVAL in d for d in cfg.deprecations)
    assert cfg == config.load(EXAMPLE)          # nothing about them is kept


def test_missing_config_file_explains_what_to_do(tmp_path):
    with pytest.raises(config.ConfigError, match="Copy config/bulk-access.example.json"):
        config.load(tmp_path / "nope.json")


# ── rules ─────────────────────────────────────────────────────────────────────
def test_inc_validation_uses_the_configured_pattern():
    cfg = config.load(EXAMPLE)
    assert rules.inc_is_valid(cfg, "INC0012345")
    assert not rules.inc_is_valid(cfg, "INC12345")
    assert not rules.inc_is_valid(cfg, "")
    custom = cfg_with(inc__pattern=r"^(INC|RITM)\d{7}$", inc__example="RITM0000001")
    assert rules.inc_is_valid(custom, "RITM0000001")


def test_launcher_access_profile_is_only_patched_where_it_differs():
    cfg = config.load(EXAMPLE)
    wanted = definitions.launcher_access_profile(cfg, "o", {"id": "ent-1", "name": "L", "source": {"id": "s"}})
    installed = {**wanted, "entitlements": [{"id": "ent-1", "type": "ENTITLEMENT"}],
                 "accessRequestConfig": {**wanted["accessRequestConfig"], "reauthorizationRequired": False}}
    assert definitions.access_profile_changes(installed, wanted) == []      # nothing to do: no PATCH
    other = [] if wanted["accessRequestConfig"]["approvalSchemes"] else [{"approverType": "MANAGER", "approverId": None}]
    old = {**installed, "description": "… with one approver …", "accessRequestConfig": {"approvalSchemes": other}}
    assert [op["path"] for op in definitions.access_profile_changes(old, wanted)] == ["/description", "/accessRequestConfig"]
    moved = {**installed, "entitlements": [{"id": "ent-2"}]}
    assert definitions.access_profile_changes(moved, wanted) == [
        {"op": "replace", "path": "/entitlements", "value": wanted["entitlements"]}]


def test_validate_request_lists_every_problem():
    cfg = config.load(EXAMPLE)
    problems = rules.validate_request(cfg, people=[], items=[], inc="nope", justification="  ")
    assert problems == ["Choose at least one person.", "Choose at least one access item.", cfg.inc_message,
                        rules.MSG_JUSTIFICATION]
    ok = rules.validate_request(cfg, people=["p1"], items=[{"id": "a", "type": "ACCESS_PROFILE"}], inc="INC0012345",
                                justification="Quarter-end audit")
    assert ok == []
    # There is no approver any more: requesting access for yourself is allowed; SailPoint's own approvals decide.
    assert rules.validate_request(cfg, people=["me"], items=[{"id": "a", "type": "ROLE"}], inc="INC0012345",
                                  justification="x") == []


def test_justification_is_capped_for_readability():
    cfg = config.load(EXAMPLE)
    item = [{"id": "a", "type": "ROLE"}]
    assert rules.validate_request(cfg, people=["p"], items=item, inc="INC0012345",
                                  justification="x" * rules.JUSTIFICATION_MAX) == []
    assert rules.validate_request(cfg, people=["p"], items=item, inc="INC0012345",
                                  justification="x" * (rules.JUSTIFICATION_MAX + 1)) == [rules.MSG_JUSTIFICATION_LONG]
    assert rules.MSG_JUSTIFICATION_LONG == "Keep the justification to 1000 characters or fewer."


def test_the_workflow_has_no_approval_of_its_own():
    # Each item is approved the way SailPoint has it set up; nothing asks one person to approve the whole request.
    for cfg in (config.load(EXAMPLE), cfg_with(mode="live")):
        wf = definitions.bulk_workflow(cfg, owner_id="o", form_id="f")
        text = json.dumps(wf)
        for gone in ("sp:generic-approval", "getApprover", "approverId", "Approver In People", "Bulk Approval",
                     "Approvals → Other"):
            assert gone not in text, gone
        assert _steps(wf)["Get Requester"]["nextStep"] == "INC Valid?"
    els = _form_elements(definitions.bulk_form(config.load(EXAMPLE), "o", []))
    assert "approver" not in els


def test_catalog_options_carry_full_access_objects_and_respect_filters():
    cfg = cfg_with(catalog__types=["ACCESS_PROFILE"], catalog__nameStartsWith="ACME")
    opts = rules.catalog_options(cfg, [
        {"id": "1", "type": "ACCESS_PROFILE", "name": "ACME Bulk Test Access", "source": {"name": "ACME SaaS"}},
        {"id": "2", "type": "ACCESS_PROFILE", "name": "Sales Regional - AD"},
        {"id": "3", "type": "ROLE", "name": "ACME Role"},
    ])
    assert opts == [{"label": "ACME Bulk Test Access", "subLabel": "Access profile · ACME SaaS",
                     "value": {"id": "1", "type": "ACCESS_PROFILE", "name": "ACME Bulk Test Access"}}]


def test_catalog_options_say_which_items_are_granted_without_approval():
    cfg = config.load(EXAMPLE)
    opts = rules.catalog_options(cfg, [{"id": "1", "type": "ACCESS_PROFILE", "name": "A", "source": {"name": "AD"}},
                                       {"id": "2", "type": "ROLE", "name": "B"}], no_approval={"2"})
    assert [o["subLabel"] for o in opts] == ["Access profile · AD", "Role · No approval: granted right away"]
    assert rules.NO_APPROVAL_LABEL == "No approval: granted right away"


def test_no_approval_ids_follow_sailpoints_rules():
    # Verified live: an access profile or role with no approval scheme is granted at once; an entitlement with none
    # of its own falls back to the tenant default (entitlementRequestConfig.grantRequestApprovalSchemes).
    objects = [{"id": "ap", "type": "ACCESS_PROFILE", "approvalSchemes": []},
               {"id": "ap2", "type": "ACCESS_PROFILE", "approvalSchemes": [{"approverType": "OWNER"}]},
               {"id": "r", "type": "ROLE", "approvalSchemes": []},
               {"id": "e", "type": "ENTITLEMENT", "approvalSchemes": []},
               {"id": "e2", "type": "ENTITLEMENT", "approvalSchemes": [{"approverType": "MANAGER"}]}]
    assert rules.no_approval_ids(objects, "sourceOwner") == {"ap", "r"}
    for empty in (None, "", "none", "NONE"):
        assert rules.no_approval_ids(objects, empty) == {"ap", "r", "e"}


def test_entitlements_come_from_the_entitlements_api_not_requestable_objects():
    # /requestable-objects can't list entitlements (CONTRACTS §8).
    assert rules.requestable_object_types(cfg_with(catalog__types=["ENTITLEMENT", "ROLE", "ACCESS_PROFILE"])) == ["ROLE", "ACCESS_PROFILE"]
    assert rules.requestable_object_types(cfg_with(catalog__types=["ENTITLEMENT"])) == []
    assert rules.entitlement_filter(cfg_with(catalog__types=["ACCESS_PROFILE"])) is None
    assert rules.entitlement_filter(cfg_with(catalog__types=["ENTITLEMENT"], catalog__nameStartsWith=None)) == "requestable eq true"
    assert (rules.entitlement_filter(cfg_with(catalog__types=["ENTITLEMENT"], catalog__nameStartsWith='AC"ME\\'))
            == 'requestable eq true and name sw "AC\\"ME\\\\"')


def test_catalog_merges_requestable_entitlements():
    calls = []

    class FakeTenant:
        def call(self, method, path, *a, **k):
            calls.append(path)
            if path.startswith("/requestable-objects/v1"):
                return [{"id": "ap", "type": "ACCESS_PROFILE", "name": "ACME AP"}]
            return [{"id": "e1", "name": "ACME Group", "source": {"name": "AD"}, "requestable": True}]

    cfg = cfg_with(catalog__types=["ACCESS_PROFILE", "ENTITLEMENT"], catalog__nameStartsWith="ACME")
    opts = rules.catalog_options(cfg, catalog.requestable_objects(FakeTenant(), cfg))
    assert [o["value"] for o in opts] == [{"id": "ap", "type": "ACCESS_PROFILE", "name": "ACME AP"},
                                         {"id": "e1", "type": "ENTITLEMENT", "name": "ACME Group"}]
    assert opts[1]["subLabel"] == "Entitlement · AD"
    assert calls[0] == "/requestable-objects/v1?types=ACCESS_PROFILE&limit=250&offset=0"
    assert calls[1].startswith("/entitlements/v1?filters=requestable%20eq%20true%20and%20name%20sw%20%22ACME%22")
    calls.clear()
    catalog.requestable_objects(FakeTenant(), cfg_with(catalog__types=["ENTITLEMENT"]))
    assert [c.split("?")[0] for c in calls] == ["/entitlements/v1"]   # no type-less requestable-objects call


def test_catalog_reads_each_items_approval_setup():
    calls = []
    schemes = {"ap1": [], "ap2": [{"approverType": "OWNER"}], "r1": []}

    class FakeTenant:
        def call(self, method, path, *a, **k):
            calls.append(path)
            if path.startswith(("/access-profiles/v1?", "/roles/v1?")):
                ids = re.findall(r"%22(\w+)%22", path)
                return [{"id": i, "accessRequestConfig": {"approvalSchemes": schemes[i]}} for i in ids]
            if path.endswith("/entitlement-request-config"):
                return {"accessRequestConfig": {"approvalSchemes": []}}
            if path == "/access-request-config/v1":
                return {"entitlementRequestConfig": {"grantRequestApprovalSchemes": "sourceOwner"}}
            raise AssertionError(path)

    objects = [{"id": "ap1", "type": "ACCESS_PROFILE"}, {"id": "ap2", "type": "ACCESS_PROFILE"},
               {"id": "r1", "type": "ROLE"}, {"id": "e1", "type": "ENTITLEMENT"}]
    assert catalog.no_approval_ids(FakeTenant(), objects) == {"ap1", "r1"}
    assert [c.split("?")[0] for c in calls] == ["/access-profiles/v1", "/roles/v1",
                                                "/entitlements/v1/e1/entitlement-request-config",
                                                "/access-request-config/v1"]
    calls.clear()
    assert catalog.no_approval_ids(FakeTenant(), objects[:1]) == {"ap1"}
    assert "/access-request-config/v1" not in calls        # no entitlements: no need for the tenant default


# ── definitions ───────────────────────────────────────────────────────────────
def _form_elements(form):
    return {e["key"]: e for e in form["formElements"][0]["config"]["formElements"]}


def test_form_enforces_inc_regex_and_required_fields():
    cfg = config.load(EXAMPLE)
    els = _form_elements(definitions.bulk_form(cfg, "owner", []))
    assert {"people", "items", "inc", "justification"} <= set(els)
    regex = next(v for v in els["inc"]["validations"] if v["validationType"] == "REGEX")
    assert regex["config"] == {"regex": cfg.inc_pattern, "message": cfg.inc_message}   # shape SailPoint accepts
    assert els["items"]["config"]["dataSource"]["dataSourceType"] == "STATIC"
    assert "approved the way it's set up in SailPoint" in els["items"]["config"]["helpText"]


def test_show_config_recommends_a_service_identity_when_owner_is_null(tmp_path, capsys):
    out = _show(tmp_path, capsys)
    assert f"Note:        {config.OWNER_NOTE}" in out
    assert "Consider a dedicated service identity in `owner`." in config.OWNER_NOTE
    out = _show(tmp_path, capsys, owner="2c9180835d2e5168015d32f890ca1581")
    assert "Owner:       2c9180835d2e5168015d32f890ca1581" in out and config.OWNER_NOTE not in out


def test_requested_email_says_each_item_is_approved_as_set_up_and_points_to_the_approvals_tab():
    _, body = _render_email(cfg_with(mode="live"), "Email Requested")
    assert "Each item is now approved the way it's set up in SailPoint" in body
    assert "items without an approval of their own are granted right away" in body
    assert f"{UI}/ui/d/approvals/access-request/requested-items" in body
    assert f'all at once on the Approvals tab of <a href="{UI}/ui/plugin/pid-1"' in body
    # No Approvals tab: the sentence stays, the pointer goes.
    for overrides in ({"approvals__enabled": False}, {"deployments__plugin": False}):
        _, body = _render_email(cfg_with(mode="live", **overrides), "Email Requested")
        assert "Item approvers decide in SailPoint" in body and "Approvals tab" not in body
    # Dry run requests nothing, so there is nothing for item approvers to decide.
    subject, body = _render_email(cfg_with(), "Email Requested")
    assert "Item approvers" not in body and "Nothing was requested, because this installation is in dry-run mode" in body
    assert subject == "Checked: bulk access INC0012345 (DRY RUN: nothing was requested)"


def _steps(wf):
    return wf["definition"]["steps"]


def test_dry_run_never_requests_access_and_live_does():
    dry = definitions.bulk_workflow(config.load(EXAMPLE), owner_id="o", form_id="f")
    assert "Request Access" not in _steps(dry)
    assert _steps(dry)["Notify Requested"]["nextStep"] == "Email Requested"
    assert "DRY RUN" in json.dumps(_steps(dry)["Email Requested"])
    live = definitions.bulk_workflow(cfg_with(mode="live"), owner_id="o", form_id="f")
    assert _steps(live)["Notify Requested"]["nextStep"] == "Request Access"
    loop = _steps(live)["Request Access"]["attributes"]
    assert _steps(live)["Request Access"]["nextStep"] == "Email Requested"
    assert loop["input.$"] == "$.interactiveForm.formData.people"      # one iteration per person
    assert loop["context.$"] == "$"                                    # steps in a loop only see $.loop.*
    manage = loop["steps"]["Manage Access"]["attributes"]
    assert manage["addIdentities.$"] == "$.loop.loopInput"
    assert manage["requestedItems.$"] == "$.loop.context.interactiveForm.formData.items"   # all items per request
    assert "{{$.loop.context.interactiveForm.formData.inc}}" in manage["comments"]


def test_the_launchpad_says_what_happened():
    live = _steps(definitions.bulk_workflow(cfg_with(mode="live"), owner_id="o", form_id="f"))["Notify Requested"]
    assert live["attributes"]["category"] == "INFO"
    assert live["attributes"]["title"] == "Requested: {{$.interactiveForm.formData.inc}}{{$.defineVariableAccess.partLabel}}"
    assert "items without an approval of their own are granted right away" in live["attributes"]["message"]
    dry = _steps(definitions.bulk_workflow(config.load(EXAMPLE), owner_id="o", form_id="f"))["Notify Requested"]
    assert dry["attributes"]["title"].startswith("Checked: ") and "Nothing was requested (DRY RUN)" in dry["attributes"]["message"]


def test_launcher_trigger_is_scoped_to_its_own_workflow():
    wf = definitions.bulk_workflow(config.load(EXAMPLE), owner_id="o", form_id="f", workflow_id="w-123")
    assert wf["trigger"]["attributes"]["id"] == "idn:interactive-process-launched"
    assert wf["trigger"]["attributes"]["filter.$"] == "$[?(@.workflowId == 'w-123')]"
    assert wf["enabled"] is False and wf["definition"]["start"] == "Interactive Form"
    with pytest.raises(ValueError):
        definitions.bulk_workflow(config.load(EXAMPLE), owner_id="o", form_id="")


def test_demo_tenants_can_redirect_all_email():
    cfg = cfg_with(notifications__overrideRecipients=["demo@example.com"])
    wf = definitions.bulk_workflow(cfg, owner_id="o", form_id="f")
    for name in ("Email Requested", "Email Reject Bad INC"):
        attrs = _steps(wf)[name]["attributes"]
        assert attrs["recipientEmailList"] == ["demo@example.com"] and "carbonCopy.$" not in attrs


def test_every_next_step_exists():
    wf = definitions.bulk_workflow(cfg_with(mode="live"), owner_id="o", form_id="f")
    steps = _steps(wf)
    targets = {s.get("nextStep") for s in steps.values()} | {s.get("defaultStep") for s in steps.values()}
    targets |= {c["nextStep"] for s in steps.values() for c in s.get("choiceList", [])}
    assert {t for t in targets if t} <= set(steps)
    assert wf["definition"]["start"] in steps


def test_another_tenant_gets_its_own_names():
    other = cfg_with(prefix="ACME", plugin__alias="acme-bulk")
    assert definitions.bulk_form(other, "o", [])["name"] == "ACME Bulk Access Request Form"
    assert definitions.bulk_workflow(other, owner_id="o", form_id="f")["name"] == "ACME Bulk Access Request"
    assert definitions.bulk_launcher(other, "w")["name"] == "ACME Bulk Access Request"


# ── lessons from the live tenant ──────────────────────────────────────────────
def test_form_people_picker_is_capped_at_sailpoints_30_selection_limit():
    cfg = cfg_with(people__max=100)
    els = _form_elements(definitions.bulk_form(cfg, "o", []))
    assert els["people"]["config"]["maximum"] == config.FORM_SELECT_MAX == 30


def test_catalog_never_offers_what_grants_the_tool_itself():
    rows = [{"id": "1", "type": "ACCESS_PROFILE", "name": "ACME Bulk Access Request - Launcher Access"},
            {"id": "2", "type": "ACCESS_PROFILE", "name": "ACME Bulk Test Access"},
            {"id": "3", "type": "ROLE", "name": "ACME Bulk Access Request - Submit Access"}]
    # With the plugin: its role (the Launcher Access profile isn't requestable then, so it isn't listed).
    opts = rules.catalog_options(cfg_with(catalog__nameStartsWith="ACME", catalog__types=["ACCESS_PROFILE", "ROLE"]),
                                 rows[1:])
    assert [o["label"] for o in opts] == ["ACME Bulk Test Access"]
    # The Launcher alone: the Launcher Access profile.
    opts = rules.catalog_options(cfg_with(catalog__nameStartsWith="ACME", deployments__plugin=False), rows[:2])
    assert [o["label"] for o in opts] == ["ACME Bulk Test Access"]


def test_launcher_access_profile_wraps_the_assigned_launchers_entitlement():
    ent = {"id": "e1", "name": "ACME Bulk Access Request", "source": {"id": "s1", "name": "IdentityNow"}}
    launcher_only = definitions.launcher_access_profile(cfg_with(deployments__plugin=False), "o", ent)
    assert launcher_only["name"] == "ACME Bulk Access Request - Launcher Access"
    assert launcher_only["entitlements"] == [{"id": "e1", "type": "ENTITLEMENT", "name": "ACME Bulk Access Request"}]
    assert launcher_only["source"]["id"] == "s1"
    assert launcher_only["requestable"] is True                       # people request it themselves
    assert launcher_only["accessRequestConfig"]["approvalSchemes"] == [{"approverType": "MANAGER"}]
    assert "Submit Access" not in launcher_only["description"]
    # With the plugin, the Submit Access role contains it: one door, so it isn't requestable on its own.
    ap = definitions.launcher_access_profile(config.load(EXAMPLE), "o", ent)
    assert ap["requestable"] is False and "Request 'ACME Bulk Access Request - Submit Access' instead" in ap["description"]
    no_approval = definitions.launcher_access_profile(cfg_with(access__approval="NONE"), "o", ent)
    assert no_approval["accessRequestConfig"]["approvalSchemes"] == []


def test_submit_role_is_requestable_needs_no_source_and_contains_the_launcher_access():
    cfg = config.load(EXAMPLE)
    access = {"id": "la-1", "name": cfg.launcher_access_profile_name}
    role = definitions.submit_role(cfg, "o", access)
    assert role["name"] == cfg.submit_role_name == cfg.submit_access_name == "ACME Bulk Access Request - Submit Access"
    assert (role["requestable"], role["enabled"], role["entitlements"]) == (True, True, [])
    assert role["accessProfiles"] == [{"id": "la-1", "type": "ACCESS_PROFILE", "name": cfg.launcher_access_profile_name}]
    assert role["accessRequestConfig"]["approvalSchemes"] == [{"approverType": "MANAGER"}]
    assert "page and the 'ACME Bulk Access Request' Launcher" in role["description"]
    alone = definitions.submit_role(cfg_with(access__approval="NONE"), "o")
    assert alone["accessProfiles"] == [] and alone["accessRequestConfig"]["approvalSchemes"] == []
    assert "Launcher" not in alone["description"]
    # Without the plugin, people request the Launcher Access profile itself.
    assert cfg_with(deployments__plugin=False).submit_access_name == "ACME Bulk Access Request - Launcher Access"


def test_role_and_profile_changes_patch_only_what_differs():
    cfg = config.load(EXAMPLE)
    wanted = definitions.submit_role(cfg, "o", {"id": "la-1", "name": "x"})
    assert definitions.role_changes({**wanted, "id": "r", "owner": {"id": "someone"}}, wanted) == []
    stale = {**wanted, "accessProfiles": [], "requestable": False,
             "accessRequestConfig": {"approvalSchemes": [], "commentsRequired": False}}
    assert [op["path"] for op in definitions.role_changes(stale, wanted)] == [
        "/accessProfiles", "/requestable", "/accessRequestConfig"]
    ent = {"id": "e1", "name": "n", "source": {"id": "s1", "name": "IdentityNow"}}
    ap = definitions.launcher_access_profile(cfg, "o", ent)
    assert [op["path"] for op in definitions.access_profile_changes({**ap, "requestable": True}, ap)] == ["/requestable"]


def test_failure_end_step_has_the_fields_the_validator_requires():
    wf = definitions.bulk_workflow(config.load(EXAMPLE), owner_id="o", form_id="f")
    end = _steps(wf)["End Step - Rejected"]
    assert end["type"] == "failure" and end["failureName"] and end["description"] and "attributes" not in end


def test_launcher_form_has_no_length_rule_on_the_justification():
    # A MAX_LENGTH validation on a form TEXTAREA silently stops submissions reaching the
    # workflow (found live), so the form must not have one.
    els = _form_elements(definitions.bulk_form(config.load(EXAMPLE), "o", []))
    assert [v["validationType"] for v in els["justification"]["validations"]] == ["REQUIRED"]



# ── people in parts ───────────────────────────────────────────────────────────
def test_split_into_parts_dedupes_in_order_and_chunks():
    people = ["a", "b", "a", "c", "d", "b", "e"]
    assert rules.split_into_parts(people, 2) == [["a", "b"], ["c", "d"], ["e"]]
    assert rules.split_into_parts(people, 250) == [["a", "b", "c", "d", "e"]]
    assert rules.split_into_parts([], 250) == []
    big = [f"p{i}" for i in range(501)]
    assert [len(p) for p in rules.split_into_parts(big)] == [250, 250, 1]     # default: SailPoint's loop limit
    with pytest.raises(ValueError):
        rules.split_into_parts(people, 0)


def test_part_label_is_empty_for_one_part():
    assert rules.part_label(1, 1) == ""
    assert rules.part_label(2, 3) == " (2/3)"
    assert rules.part_label(1, 2) == " (1/2)"


# ── temporary access ──────────────────────────────────────────────────────────
UTC = timezone.utc
NOW = datetime(2026, 10, 8, 10, 0, 0, tzinfo=UTC)


def test_permanent_is_always_allowed_and_carries_an_empty_duration():
    for cfg in (config.load(EXAMPLE), cfg_with(temporaryAccess__enabled=False)):
        for route in ("launcher", "plugin"):
            assert rules.validate_access(cfg, "permanent", route=route) == []
            assert rules.access_choice(cfg, "permanent", route=route) == rules.AccessChoice("", "Permanent")


@pytest.mark.parametrize("n,unit,duration,label", [
    (1, "DAYS", "1d", "Temporary: 1 day"),
    (30, "DAYS", "30d", "Temporary: 30 days"),
    ("2", "HOURS", "2h", "Temporary: 2 hours"),
    (1, "WEEKS", "1w", "Temporary: 1 week"),
    (3, "MONTHS", "3M", "Temporary: 3 months"),
])
def test_duration_becomes_a_remove_duration_and_a_label(n, unit, duration, label):
    choice = rules.access_choice(config.load(EXAMPLE), "duration", n=n, unit=unit)
    assert choice == rules.AccessChoice(duration, label)


def test_temporary_access_unavailable_message():
    msg = ["Temporary access isn't available."]
    off = cfg_with(temporaryAccess__enabled=False)
    assert rules.validate_access(off, "duration", n=1, unit="DAYS") == msg
    assert rules.validate_access(off, "endDate", end_date="2026-10-10", now=NOW, tz=UTC) == msg
    # The Launcher never offers an end date (a workflow can't turn a date into a duration).
    assert rules.validate_access(config.load(EXAMPLE), "endDate", end_date="2026-10-10", route="launcher", now=NOW, tz=UTC) == msg
    only_end = cfg_with(temporaryAccess__allow=["endDate"])
    assert rules.validate_access(only_end, "duration", n=1, unit="DAYS") == msg
    assert rules.validate_access(config.load(EXAMPLE), "forever") == msg


@pytest.mark.parametrize("n", [0, -1, "0", "abc", "", None, 1.5, True, "1.5"])
def test_duration_must_be_a_whole_number_of_at_least_one(n):
    assert rules.validate_access(config.load(EXAMPLE), "duration", n=n, unit="DAYS") == \
        ["Enter the duration as a whole number of 1 or more."]


def test_duration_unit_must_be_an_allowed_unit():
    cfg = cfg_with(temporaryAccess__units=["DAYS", "WEEKS"])
    assert rules.validate_access(cfg, "duration", n=2, unit="HOURS") == ["Choose a unit for the duration."]
    assert rules.validate_access(cfg, "duration", n=2, unit=None) == ["Choose a unit for the duration."]
    assert rules.validate_access(cfg, "duration", n="x", unit=None) == [
        "Enter the duration as a whole number of 1 or more.", "Choose a unit for the duration."]


def test_max_days_caps_every_unit():
    cfg = cfg_with(temporaryAccess__maxDays=7)
    cap = ["Temporary access can last at most 7 days."]
    assert rules.validate_access(cfg, "duration", n=7, unit="DAYS") == []
    assert rules.validate_access(cfg, "duration", n=8, unit="DAYS") == cap
    assert rules.validate_access(cfg, "duration", n=168, unit="HOURS") == []
    assert rules.validate_access(cfg, "duration", n=169, unit="HOURS") == cap
    assert rules.validate_access(cfg, "duration", n=1, unit="WEEKS") == []
    assert rules.validate_access(cfg, "duration", n=2, unit="WEEKS") == cap
    assert rules.validate_access(cfg, "duration", n=1, unit="MONTHS") == cap          # a month counts as 31 days
    assert rules.validate_access(cfg_with(temporaryAccess__maxDays=31), "duration", n=1, unit="MONTHS") == []


def test_end_date_is_converted_to_hours_until_the_end_of_that_local_day():
    cfg = config.load(EXAMPLE)
    # 10:00 UTC on the 8th -> 23:59:59 on the 9th is 37 h 59 min 59 s: rounded up to 38 h.
    assert rules.end_date_hours("2026-10-09", now=NOW, tz=UTC) == 38
    assert rules.access_choice(cfg, "endDate", end_date="2026-10-09", now=NOW, tz=UTC) == \
        rules.AccessChoice("38h", "Temporary: until 2026-10-09")
    # The same instant seen from UTC-7 (03:00 local): the local day ends 7 hours later.
    pdt = timezone(timedelta(hours=-7))
    assert rules.access_choice(cfg, "endDate", end_date=date(2026, 10, 9), now=NOW, tz=pdt).remove_duration == "45h"
    assert rules.end_date_hours("2026-11-07", now=NOW, tz=UTC) == 30 * 24 + 14


def test_end_date_uses_dst_aware_local_time_when_a_zone_is_given():
    zoneinfo = pytest.importorskip("zoneinfo")
    try:
        la = zoneinfo.ZoneInfo("America/Los_Angeles")
    except zoneinfo.ZoneInfoNotFoundError:
        pytest.skip("no time zone database")
    now = datetime(2026, 10, 31, 12, 0, tzinfo=la)          # PDT; DST ends on 1 November
    # Until 23:59:59 PST on 1 November: 12 h + 24 h + the extra hour, rounded up.
    assert rules.end_date_hours("2026-11-01", now=now, tz=la) == 37


def test_end_date_must_be_after_today():
    cfg = config.load(EXAMPLE)
    msg = ["Choose an end date after today."]
    assert rules.validate_access(cfg, "endDate", end_date="2026-10-08", now=NOW, tz=UTC) == msg     # today
    assert rules.validate_access(cfg, "endDate", end_date="2026-10-01", now=NOW, tz=UTC) == msg     # past
    assert rules.validate_access(cfg, "endDate", end_date="10/09/2026", now=NOW, tz=UTC) == msg     # not YYYY-MM-DD
    assert rules.validate_access(cfg, "endDate", end_date="2026-02-30", now=NOW, tz=UTC) == msg
    assert rules.validate_access(cfg, "endDate", end_date=None, now=NOW, tz=UTC) == msg
    # `today` can be injected separately (e.g. the user's calendar date).
    assert rules.validate_access(cfg, "endDate", end_date="2026-10-09", now=NOW, today=date(2026, 10, 9), tz=UTC) == msg


def test_end_date_respects_max_days():
    cap1 = cfg_with(temporaryAccess__maxDays=1)
    # Tomorrow from 10:00 is 38 h away: more than 1 day.
    assert rules.validate_access(cap1, "endDate", end_date="2026-10-09", now=NOW, tz=UTC) == \
        ["Temporary access can last at most 1 days."]
    late = datetime(2026, 10, 8, 23, 59, 59, tzinfo=UTC)    # exactly 24 h before the end of tomorrow
    assert rules.validate_access(cap1, "endDate", end_date="2026-10-09", now=late, tz=UTC) == []


def test_access_choice_raises_the_first_problem():
    with pytest.raises(ValueError, match="whole number"):
        rules.access_choice(config.load(EXAMPLE), "duration", n=0, unit="DAYS")


# ── regexes the Launcher workflow uses for maxDays ────────────────────────────
@pytest.mark.parametrize("maximum", [*range(1, 130), 168, 199, 200, 239, 240, 744, 999, 1000, 1009, 2190, 9999])
def test_int_range_regex_matches_exactly_one_to_maximum(maximum):
    pattern = re.compile(f"^(?:{rules.int_range_regex(maximum)})$")
    for k in range(0, maximum + 40):
        assert bool(pattern.match(str(k))) == (1 <= k <= maximum), (maximum, k)
    assert not pattern.match("01") and not pattern.match("") and not pattern.match("-1")


def test_duration_regex_combines_units_and_max_days():
    pattern = re.compile(rules.duration_regex(["HOURS", "DAYS", "WEEKS", "MONTHS"], 7))
    for ok in ("1h", "168h", "7d", "1w"):
        assert pattern.match(ok), ok
    for bad in ("169h", "8d", "2w", "1M", "", "7", "d", "0d", "07d", "7D"):
        assert not pattern.match(bad), bad
    unlimited = re.compile(rules.duration_regex(["DAYS"], None, allow_empty=True))
    assert unlimited.match("") and unlimited.match("365d") and not unlimited.match("3h")


# ── definitions: temporary access and parts ───────────────────────────────────
LIVE = {"mode": "live"}


def _manage(wf):
    return _steps(wf)["Request Access"]["attributes"]["steps"]["Manage Access"]


def test_manage_access_is_version_2_with_remove_duration():
    launcher = _manage(definitions.bulk_workflow(cfg_with(**LIVE), owner_id="o", form_id="f"))
    assert launcher["versionNumber"] == 2
    assert launcher["attributes"]["removeDuration.$"] == "$.loop.context.defineVariableAccess.removeDuration"
    # Still used when temporary access is off ("" = permanent, always set).
    off = _manage(definitions.bulk_workflow(cfg_with(mode="live", temporaryAccess__enabled=False), owner_id="o", form_id="f"))
    assert off["attributes"]["removeDuration.$"] == "$.loop.context.defineVariableAccess.removeDuration"


def test_item_comment_keeps_the_inc_first_and_adds_the_access_label():
    comment = _manage(definitions.bulk_workflow(cfg_with(**LIVE), owner_id="o", form_id="f"))["attributes"]["comments"]
    assert comment.split(" | ") == [
        "{{$.loop.context.interactiveForm.formData.inc}}",
        "Bulk access request by {{$.loop.context.getRequester.attributes.displayName}}",
        "{{$.loop.context.defineVariableAccess.accessLabel}}", "{{$.loop.context.interactiveForm.formData.justification}}"]


def test_emails_say_which_part_and_the_access_label():
    steps = _steps(definitions.bulk_workflow(cfg_with(mode="live"), owner_id="o", form_id="f"))
    attrs = steps["Email Requested"]["attributes"]
    assert "{{$.defineVariableAccess.partLabel}}" in attrs["subject"]
    assert attrs["context"]["part.$"] == "$.defineVariableAccess.partLabel"
    assert attrs["context"]["access.$"] == "$.defineVariableAccess.accessLabel"
    subject, body = _render_email(cfg_with(mode="live"), "Email Requested", part=" (2/3)", access="Temporary: 720h")
    assert subject == "Requested: bulk access INC0012345 (2/3)"
    assert "(2/3): one part of a larger request with the same INC" in body
    assert "Temporary: 720h (removed automatically when it ends)" in body


def test_launcher_form_offers_temporary_access_only_when_configured():
    els = _form_elements(definitions.bulk_form(config.load(EXAMPLE), "o", []))
    assert els["accessType"]["elementType"] == "TOGGLE" and els["accessType"]["config"]["default"] is False
    assert (els["accessType"]["config"]["falseLabel"], els["accessType"]["config"]["trueLabel"]) == ("Permanent", "Temporary")
    assert els["duration"]["elementType"] == "TEXT"
    assert "REQUIRED" not in [v["validationType"] for v in els["duration"]["validations"]]   # hidden while Permanent
    units = els["durationUnit"]["config"]
    assert units["maximum"] == 1
    assert [o["value"] for o in units["dataSource"]["config"]["options"]] == ["h", "d", "w", "M"]
    form = definitions.bulk_form(config.load(EXAMPLE), "o", [])
    effects = form["formConditions"][0]["effects"]
    assert {e["config"]["element"] for e in effects} == {"duration", "durationUnit"}

    for off in (cfg_with(temporaryAccess__enabled=False), cfg_with(temporaryAccess__allow=["endDate"])):
        form = definitions.bulk_form(off, "o", [])
        assert set(_form_elements(form)) == {"people", "items", "inc", "justification"}
        assert form["formConditions"] == []

    # A unit that can never fit maxDays isn't offered.
    short = _form_elements(definitions.bulk_form(cfg_with(temporaryAccess__maxDays=7), "o", []))
    assert [o["value"] for o in short["durationUnit"]["config"]["dataSource"]["config"]["options"]] == ["h", "d", "w"]


def test_launcher_checks_the_duration_before_requesting_anything():
    steps = _steps(definitions.bulk_workflow(cfg_with(temporaryAccess__maxDays=7, mode="live"), owner_id="o", form_id="f"))
    assert steps["INC Valid?"]["choiceList"][0]["nextStep"] == "Define Variable Access"
    assert steps["Define Variable Access"]["type"] == "Mutation"     # "Define Variable" prefix: Update Variable needs it
    assert steps["Temporary?"]["actionId"] == "sp:compare-boolean"
    assert steps["Temporary?"]["choiceList"][0]["variableA.$"] == "$.interactiveForm.formData.accessType"
    assert steps["Temporary?"]["defaultStep"] == "Notify Requested"    # permanent goes straight on
    assert steps["Duration Valid?"]["choiceList"][0]["variableB"] == "^[1-9][0-9]*$"
    assert steps["Unit Valid?"]["choiceList"][0]["variableB"] == "^(?:h|d|w)$"
    updated = {v["name"] for v in steps["Set Temporary Access"]["attributes"]["variables"]}
    assert updated == {"$.defineVariableAccess.removeDuration", "$.defineVariableAccess.accessLabel"}
    limit = steps["Within Limit?"]["choiceList"][0]
    assert limit["variableA.$"] == "$.defineVariableAccess.removeDuration"
    assert re.match(limit["variableB"], "168h") and not re.match(limit["variableB"], "8d")
    messages = {name: steps[name]["attributes"]["message"] for name in ("Reject Bad Duration", "Reject Bad Unit", "Reject Too Long")}
    after = "<p>Nothing was requested. Fix it and submit again; you'll also get this by email.</p>"
    assert messages == {"Reject Bad Duration": "<p>Duration: Enter the duration as a whole number of 1 or more.</p>" + after,
                        "Reject Bad Unit": "<p>Unit: Choose a unit for the duration.</p>" + after,
                        "Reject Too Long": "<p>Duration: Temporary access can last at most 7 days.</p>" + after}
    # Every path to the requests passes the checks: only Notify Requested leads to them.
    assert [n for n, s in steps.items() if s.get("nextStep") == "Request Access"] == ["Notify Requested"]


def test_launcher_without_max_days_or_temporary_access_has_no_extra_checks():
    steps = _steps(definitions.bulk_workflow(config.load(EXAMPLE), owner_id="o", form_id="f"))
    assert "Within Limit?" not in steps and steps["Set Temporary Access"]["nextStep"] == "Notify Requested"
    off = _steps(definitions.bulk_workflow(cfg_with(temporaryAccess__enabled=False), owner_id="o", form_id="f"))
    assert "Temporary?" not in off and off["Define Variable Access"]["nextStep"] == "Part Given?"
    assert off["Part Given?"]["defaultStep"] == off["Set Part"]["nextStep"] == "Notify Requested"


@pytest.mark.parametrize("overrides", [
    {}, {"mode": "live"}, {"mode": "live", "temporaryAccess__maxDays": 7}, {"temporaryAccess__enabled": False},
    {"mode": "live", "temporaryAccess__allow": ["endDate"]}, {"mode": "live", "temporaryAccess__units": ["DAYS"]},
])
def test_the_workflow_is_a_complete_graph(overrides):
    wf = definitions.bulk_workflow(cfg_with(**overrides), owner_id="o", form_id="f", workflow_id="w")
    steps = _steps(wf)
    targets = {s.get("nextStep") for s in steps.values()} | {s.get("defaultStep") for s in steps.values()}
    targets |= {c["nextStep"] for s in steps.values() for c in s.get("choiceList", [])}
    assert {t for t in targets if t} <= set(steps), overrides
    assert wf["definition"]["start"] in steps
    reachable, todo = set(), [wf["definition"]["start"]]
    while todo:
        name = todo.pop()
        if name in reachable:
            continue
        reachable.add(name)
        s = steps[name]
        todo += [t for t in [s.get("nextStep"), s.get("defaultStep"), *[c["nextStep"] for c in s.get("choiceList", [])]] if t]
    assert reachable == set(steps), (overrides, set(steps) - reachable)
    for s in steps.values():
        if s.get("type") == "failure":
            assert s["failureName"] and s["description"]


# ── config compatibility ──────────────────────────────────────────────────────
def test_old_access_approval_keys_still_work_with_a_note():
    data = json.loads(EXAMPLE.read_text())
    del data["access"]
    data["launcher"] = {"accessApproval": "NONE"}
    cfg = config.from_dict(data)
    assert cfg.access_approval == "NONE"
    assert any("launcher.accessApproval" in d and "access.approval" in d for d in cfg.deprecations)
    data = json.loads(EXAMPLE.read_text())
    data["access"] = {"launcherApproval": "NONE"}
    cfg = config.from_dict(data)
    assert cfg.access_approval == "NONE" and cfg.deprecations == ("`access.launcherApproval` is now `access.approval`.",)
    data["access"] = {"approval": "MANAGER", "launcherApproval": "NONE"}     # the new key wins
    assert config.from_dict(data).access_approval == "MANAGER"
    assert config.load(EXAMPLE).deprecations == ()


# ── the bulkaccess.py CLI ─────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[2]


def _cli():
    spec = importlib.util.spec_from_file_location("bulkaccess_cli", ROOT / "bulkaccess.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _config_file(tmp_path, **overrides):
    data = json.loads(EXAMPLE.read_text())
    for dotted, value in overrides.items():
        target = data
        *parents, leaf = dotted.split("__")
        for part in parents:
            target = target.setdefault(part, {})
        target[leaf] = value
    path = tmp_path / "tenant.json"
    path.write_text(json.dumps(data))
    return str(path)


def _show(tmp_path, capsys, **overrides):
    assert _cli().main(["show-config", "--config", _config_file(tmp_path, **overrides)]) == 0
    return capsys.readouterr().out


def test_show_config_for_both_deployments(tmp_path, capsys):
    out = _show(tmp_path, capsys)
    assert "People:      no limit, 10 people per access request (plugin) · up to 30 (Launcher form, SailPoint limit)" in out
    assert "  launcher:  duration in hours, days, weeks, months (no end date" in out
    assert "  plugin:    duration in hours, days, weeks, months or end date\n" in out
    assert "Approval:    each item the way it's set up in SailPoint" in out
    assert "Deployments: launcher, plugin\n" in out and "Deprecated" not in out


def test_show_config_launcher_only(tmp_path, capsys):
    out = _show(tmp_path, capsys, deployments__plugin=False, people__max=10, launcher={"accessApproval": "NONE"})
    assert "People:      up to 10 (Launcher form)" in out
    assert "(plugin off)" in out and "  plugin:" not in out
    assert "Deprecated:  `launcher.accessApproval` is now `access.approval`." in out
    assert "Access:      people request 'ACME Bulk Access Request - Launcher Access' in the Request Center; " \
           "approval: their manager" in out                                 # the example's access.approval wins


def test_show_config_plugin_only(tmp_path, capsys):
    out = _show(tmp_path, capsys, deployments__launcher=False, people__max=600, temporaryAccess__maxDays=14,
                temporaryAccess__allow=["endDate"])
    assert "People:      up to 600, 10 people per access request (plugin)" in out
    assert "Launcher form" not in out and "(launcher off)" in out
    assert "Temporary:   at most 14 days" in out
    assert "  plugin:    end date\n" in out and "  launcher:" not in out
    assert "holders of the role 'ACME Bulk Access Request - Submit Access' can submit" in out and "Email:" not in out
    assert "  plugin:    role 'ACME Bulk Access Request - Submit Access'\n" in out


def test_show_config_through_the_launcher_sends_parts(tmp_path, capsys):
    out = _show(tmp_path, capsys, plugin__submit="launcher", people__max=600, people__partSize=200)
    assert "People:      up to 600, sent as Launcher runs of up to 200 (plugin)" in out
    out = _show(tmp_path, capsys, plugin__submit="launcher", people__max=100)
    assert "People:      up to 100, in one Launcher run (plugin)" in out
    assert "  plugin:    duration in hours, days, weeks, months or end date (sent as hours)" in out


def test_apply_runs_only_enabled_deployments_with_the_right_flags(tmp_path, monkeypatch):
    cli = _cli()
    calls = []
    monkeypatch.setattr(cli, "run_script", lambda d, script, argv, **k: calls.append((d, script, argv)) or 0)
    both = _config_file(tmp_path, plugin__public=True)
    assert cli.main(["apply", "--config", both, "--dry-run", "--grant", "me"]) == 0
    # --grant goes to the plugin installer: its role is what people hold (it includes the Launcher)
    assert calls == [("launcher", "install", ["--config", both, "--dry-run"]),
                     ("plugin", "install", ["--config", both, "--dry-run", "--grant", "me", "--public"])]
    calls.clear()
    private = _config_file(tmp_path)
    assert cli.main(["apply", "--config", private, "--only", "plugin", "--deploy"]) == 0
    assert calls == [("plugin", "install", ["--config", private, "--deploy"])]
    calls.clear()
    launcher_only = _config_file(tmp_path, deployments__plugin=False)
    assert cli.main(["status", "--config", launcher_only]) == 0
    assert calls == [("launcher", "status", ["--config", launcher_only])]
    assert cli.main(["apply", "--config", launcher_only, "--grant", "me"]) == 0
    assert calls[-1] == ("launcher", "install", ["--config", launcher_only, "--grant", "me"])
    with pytest.raises(config.ConfigError, match="switched off"):
        cli.main(["apply", "--config", launcher_only, "--only", "plugin"])


def test_uninstall_previews_unless_yes(tmp_path, monkeypatch):
    cli = _cli()
    calls = []
    monkeypatch.setattr(cli, "run_script", lambda d, script, argv, **k: calls.append((d, argv, k.get("answer"))) or 0)
    path = _config_file(tmp_path)
    assert cli.main(["uninstall", "--config", path]) == 0
    assert calls == [("launcher", ["--config", path], None), ("plugin", ["--config", path, "--plugin"], "no")]
    calls.clear()
    assert cli.main(["uninstall", "--config", path, "--yes", "--only", "launcher"]) == 0
    assert calls == [("launcher", ["--config", path, "--yes"], None)]
    calls.clear()   # retiring a deployment: switch it off, apply, then uninstall it by name
    plugin_only = _config_file(tmp_path, deployments__launcher=False)
    assert cli.main(["uninstall", "--config", plugin_only, "--yes", "--only", "launcher"]) == 0
    assert calls == [("launcher", ["--config", plugin_only, "--yes"], None)]


def test_cli_loads_both_folders_scripts_without_mixing_them_up():
    cli = _cli()
    with cli._script_dir(ROOT / "launcher"):
        launcher_install = cli.load_script("launcher", "install")
    with cli._script_dir(ROOT / "plugin"):
        plugin_install = cli.load_script("plugin", "install")
    assert hasattr(launcher_install, "find_form") and not hasattr(plugin_install, "find_form")
    assert Path(plugin_install.__file__).parent.name == "plugin"


def test_export_offline_builds_every_object_with_placeholders(tmp_path):
    cli = _cli()
    assert cli.main(["export", "--offline", "--config", str(EXAMPLE), "--out", str(tmp_path)]) == 0
    files = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*.json"))
    # The plugin has no workflow of its own: it files requests directly, or goes through the Launcher's.
    assert files == ["launcher/access-profile.json", "launcher/form.json", "launcher/launcher.json",
                     "launcher/workflow.json", "plugin/submit-role.json"]
    role = json.loads((tmp_path / "plugin/submit-role.json").read_text())
    assert role["accessProfiles"][0]["id"] == "<LAUNCHER_ACCESS_PROFILE_ID>"
    wf = json.loads((tmp_path / "launcher/workflow.json").read_text())
    assert wf["definition"]["steps"]["Interactive Form"]["attributes"]["formDefinitionId"] == "<FORM_DEFINITION_ID>"
    assert wf["trigger"]["attributes"]["filter.$"] == "$[?(@.workflowId == '<WORKFLOW_ID>')]"


# ── bulk approvals: the item comment read back, and the `approvals` config ────
BULK = "INC0012345 | Bulk access request by Ada Lovelace | Temporary: 30 days | Quarterly audit"
PARSED = {"inc": "INC0012345", "requester": "Ada Lovelace", "approver": None,
          "accessLabel": "Temporary: 30 days", "justification": "Quarterly audit"}
# Written while bulk requests had one approver: still read, so approvals already pending keep their INC grouping.
OLD = "INC0012345 | Bulk access request by Ada Lovelace | Approved by Grace Hopper | Temporary: 30 days | Quarterly audit"


def _bulk(justification, label="Permanent", approver=None):
    return {"inc": "INC0012345", "requester": "Ada", "approver": approver, "accessLabel": label,
            "justification": justification}


def test_comment_separator_is_what_definitions_writes():
    assert rules.COMMENT_SEPARATOR == " | "


@pytest.mark.parametrize("text,expected", [
    (BULK, PARSED),
    (OLD, {**PARSED, "approver": "Grace Hopper"}),
    # the justification is everything after the 3rd separator, " | " included
    ("INC0012345 | Bulk access request by Ada | Permanent | move | to | finance", _bulk("move | to | finance")),
    ("INC0012345 | Bulk access request by Ada | Approved by Grace | Permanent | move | to | finance",
     _bulk("move | to | finance", approver="Grace")),
    # extra whitespace around the separators and inside the labels
    ("  INC0012345   |  Bulk  access request   by  Ada Lovelace |  Temporary: 30 days  |  Quarterly audit  ", PARSED),
    # an empty justification (a trailing separator, with or without its space)
    ("INC0012345 | Bulk access request by Ada | Permanent | ", _bulk("")),
    ("INC0012345 | Bulk access request by Ada | Permanent |", _bulk("")),
    # a multi-line justification is kept as written
    ("INC0012345 | Bulk access request by Ada | Permanent | line one\nline two", _bulk("line one\nline two")),
])
def test_parse_bulk_comment_reads_the_bulk_item_comment(text, expected):
    assert rules.parse_bulk_comment(config.load(EXAMPLE), text) == expected


def test_bulk_comment_is_what_parse_bulk_comment_reads():
    text = rules.bulk_comment(" INC0012345 ", "Ada Lovelace", "Temporary: 30 days", " Quarterly audit ")
    assert text == BULK
    assert rules.parse_bulk_comment(config.load(EXAMPLE), text) == PARSED


@pytest.mark.parametrize("text", [
    None, "", 42, "INC0012345", "INC0012345: Quarterly audit",          # plain comments, the INC alone
    "Please approve, thanks",
    "INC0012345 | Bulk access request by Ada | Permanent",                              # only 3 fields
    "INC12345 | Bulk access request by Ada | Permanent | why",                           # INC fails the pattern
    "CHG0012345 | Bulk access request by Ada | Permanent | why",
    "INC0012345 | Access request by Ada | Permanent | why",                              # wrong label
    "INC0012345 | Permanent | Bulk access request by Ada | why",                         # wrong order
    "INC0012345 | Bulk access request by | Permanent | why",                             # empty name or label
    "INC0012345 | Bulk access request by Ada |  | why",
    "INC0012345 | Bulk access request byAda | Permanent | why",
    "INC0012345 | Bulk access request by Ada | Approved by  | Permanent | why",          # old form, empty approver
])
def test_parse_bulk_comment_ignores_everything_else(text):
    assert rules.parse_bulk_comment(config.load(EXAMPLE), text) is None


def test_parse_bulk_comment_uses_the_configured_inc_pattern():
    cfg = cfg_with(inc__pattern=r"^(INC|RITM)\d{7}$", inc__example="RITM0000001")
    text = BULK.replace("INC0012345", "RITM0000001", 1)
    assert rules.parse_bulk_comment(cfg, text)["inc"] == "RITM0000001"
    assert rules.parse_bulk_comment(config.load(EXAMPLE), text) is None


def _render(template, values):
    """Fill the workflow's {{$.path}} templates as SailPoint would, choosing each value by a part of its path."""
    def value(m):
        path = m.group(1)
        for key, val in values.items():
            if key in path:
                return val
        raise AssertionError(f"unexpected template {path}")
    return re.sub(r"\{\{(\$[^}]*)\}\}", value, template)


def test_the_comment_definitions_writes_parses_back_to_its_inputs():
    cfg = cfg_with(**LIVE)
    template = _manage(definitions.bulk_workflow(cfg, owner_id="o", form_id="f"))["attributes"]["comments"]
    expected = {"inc": "INC0012345", "requester": "Ada Lovelace", "approver": None,
                "accessLabel": "Temporary: 720h", "justification": "Audit | see INC0099999: finance | Q4"}
    rendered = _render(template, {"getRequester": expected["requester"], "accessLabel": expected["accessLabel"],
                                  "justification": expected["justification"], "inc": expected["inc"]})
    assert rendered == rules.bulk_comment(expected["inc"], expected["requester"], expected["accessLabel"],
                                          expected["justification"])
    assert rules.parse_bulk_comment(cfg, rendered) == expected


def test_approvals_defaults_and_derived_values():
    cfg = config.load(EXAMPLE)
    assert (cfg.approvals_enabled, cfg.approvals_concurrency, cfg.approvals_use_bulk_endpoint, cfg.approvals_max_rows,
            cfg.approvals_show_other, cfg.approvals_deny_comment_required) == (True, 4, "auto", 5000, False, True)
    assert cfg.plugin_approvals_enabled
    data = json.loads(EXAMPLE.read_text())
    del data["approvals"]                                                    # a config from before the block
    assert config.from_dict(data) == cfg
    assert not cfg_with(deployments__plugin=False).plugin_approvals_enabled   # the tab lives in the plugin
    assert not cfg_with(approvals__enabled=False).plugin_approvals_enabled
    custom = cfg_with(approvals={"concurrency": 8, "useBulkEndpoint": "never", "maxRows": 250, "showOther": True,
                                 "denyCommentRequired": False})
    assert (custom.approvals_enabled, custom.approvals_concurrency, custom.approvals_use_bulk_endpoint,
            custom.approvals_max_rows, custom.approvals_show_other, custom.approvals_deny_comment_required) == \
        (True, 8, "never", 250, True, False)
    assert cfg_with(approvals__concurrency=1, approvals__maxRows=20000).approvals_max_rows == 20000


@pytest.mark.parametrize("key,value,message", [
    ("approvals", "on", "`approvals` must be an object."),
    ("approvals__enabled", "yes", "`approvals.enabled` must be true or false."),
    ("approvals__showOther", 1, "`approvals.showOther` must be true or false."),
    ("approvals__denyCommentRequired", None, "`approvals.denyCommentRequired` must be true or false."),
    ("approvals__concurrency", 0, "`approvals.concurrency` must be a whole number between 1 and 8."),
    ("approvals__concurrency", 9, "`approvals.concurrency` must be a whole number between 1 and 8."),
    ("approvals__concurrency", 2.5, "`approvals.concurrency` must be a whole number between 1 and 8."),
    ("approvals__concurrency", True, "`approvals.concurrency` must be a whole number between 1 and 8."),
    ("approvals__useBulkEndpoint", "sometimes", '`approvals.useBulkEndpoint` must be "auto", "always" or "never".'),
    ("approvals__maxRows", 249, "`approvals.maxRows` must be a whole number between 250 and 20000."),
    ("approvals__maxRows", 20001, "`approvals.maxRows` must be a whole number between 250 and 20000."),
    ("approvals__maxRows", "5000", "`approvals.maxRows` must be a whole number between 250 and 20000."),
])
def test_bad_approvals_config_is_rejected_with_the_exact_message(key, value, message):
    with pytest.raises(config.ConfigError) as err:
        cfg_with(**{key: value})
    assert str(err.value) == message


def test_show_config_describes_approvals_and_warns_about_a_private_plugin(tmp_path, capsys):
    warning = ("Warning:     non-admin approvers can't open a private plugin unless they are listed in the plugin's "
               "restrictToUsers")
    out = _show(tmp_path, capsys)
    assert "Approvals:   Approvals tab in the plugin: 4 at a time, bulk endpoint when allowed, up to 5000 pending rows" in out
    assert warning in out
    out = _show(tmp_path, capsys, plugin__public=True)
    assert "Approvals:   Approvals tab" in out and "Warning:" not in out
    out = _show(tmp_path, capsys, approvals__enabled=False)
    assert "Approvals:   off (approvals.enabled is false)" in out and "Warning:" not in out
    out = _show(tmp_path, capsys, deployments__plugin=False)
    assert "Approvals:   off (the Approvals tab is part of the plugin, which is off)" in out and "Warning:" not in out


# ── plugin.submit: the plugin submits through the Launcher (CONTRACTS §9) ─────────
def test_plugin_submit_defaults_to_direct():
    cfg = config.load(EXAMPLE)
    assert cfg.plugin_submit == "direct" and cfg.plugin_submits_directly and not cfg.plugin_submits_via_launcher
    assert cfg.launcher_access_profile_name == "ACME Bulk Access Request - Launcher Access"
    assert cfg_with(plugin__submit=None).plugin_submit == "direct"
    plugin_only = cfg_with(deployments={"launcher": False, "plugin": True})
    assert plugin_only.plugin_submits_directly
    via = cfg_with(plugin__submit="launcher")
    assert via.plugin_submits_via_launcher and not via.plugin_submits_directly
    launcher_only = cfg_with(deployments={"launcher": True, "plugin": False})
    assert not launcher_only.plugin_submits_via_launcher and not launcher_only.plugin_submits_directly


def test_the_old_test_endpoint_mode_becomes_direct_with_a_note():
    cfg = cfg_with(plugin__submit="test-endpoint")
    assert cfg.plugin_submit == "direct"
    assert cfg.deprecations == ('`plugin.submit` "test-endpoint" is now "direct": the plugin files the access requests '
                                "itself, so it no longer needs its own workflow.",)


@pytest.mark.parametrize("overrides,message", [
    ({"plugin__submit": "backend"}, config.MSG_PLUGIN_SUBMIT),
    ({"plugin__submit": "launcher", "deployments": {"launcher": False, "plugin": True}}, config.MSG_PLUGIN_SUBMIT_LAUNCHER),
])
def test_plugin_submit_is_validated(overrides, message):
    with pytest.raises(config.ConfigError) as err:
        cfg_with(**overrides)
    assert str(err.value) == message


def test_through_the_launcher_the_plugin_offers_only_what_the_launcher_form_carries():
    # Direct requests carry an exact removal date: every configured mode works.
    assert cfg_with().plugin_temporary_modes == ("duration", "endDate")
    assert cfg_with(temporaryAccess__allow=["endDate"]).plugin_temporary_modes == ("endDate",)
    # Through the Launcher: durations keep the end date (sent as hours in the same fields) ...
    assert cfg_with(plugin__submit="launcher").plugin_temporary_modes == ("duration", "endDate")
    # ... but without duration fields on the Launcher form, only permanent access.
    assert cfg_with(plugin__submit="launcher", temporaryAccess__allow=["endDate"]).plugin_temporary_modes == ()
    assert cfg_with(plugin__submit="launcher", temporaryAccess__units=["WEEKS"],
                    temporaryAccess__maxDays=5).plugin_temporary_modes == ()
    assert cfg_with(temporaryAccess__enabled=False).launcher_temporary_modes == ()


def test_launcher_workflow_takes_hours_from_the_plugin_for_end_dates():
    cfg = cfg_with(temporaryAccess__units=["DAYS", "WEEKS"], plugin__submit="launcher")
    assert definitions.launcher_duration_units(cfg) == ("DAYS", "WEEKS")          # what the Launchpad offers
    assert definitions.launcher_workflow_units(cfg) == ("HOURS", "DAYS", "WEEKS")  # what the workflow accepts
    steps = _steps(definitions.bulk_workflow(cfg, owner_id="o", form_id="f"))
    assert steps["Unit Valid?"]["choiceList"][0]["variableB"] == "^(?:h|d|w)$"
    options = _form_elements(definitions.bulk_form(cfg, "o", []))["durationUnit"]["config"]["dataSource"]["config"]["options"]
    assert [o["value"] for o in options] == ["d", "w"]                            # the Launchpad sees no change
    for same in (cfg_with(temporaryAccess__units=["DAYS"]),                         # direct: the plugin doesn't use it
                 cfg_with(temporaryAccess__units=["DAYS"], temporaryAccess__allow=["duration"], plugin__submit="launcher")):
        assert definitions.launcher_workflow_units(same) == ("DAYS",)


def test_the_workflow_checks_a_part_label_the_launcher_form_has_no_field_for():
    # A HIDDEN element makes the Launchpad render an empty form (verified live), so the plugin sends partLabel as an
    # extra form data key and the form has no field for it.
    els = _form_elements(definitions.bulk_form(config.load(EXAMPLE), "o", []))
    assert definitions.F_PART_LABEL not in els
    assert all(e.get("elementType") != "HIDDEN" for e in els.values())
    steps = _steps(definitions.bulk_workflow(config.load(EXAMPLE), owner_id="o", form_id="f"))
    define = {v["name"]: v for v in steps["Define Variable Access"]["attributes"]["variables"]}
    # Defined as a placeholder emptied by a replace transform (a literal "" can't be defined).
    assert define["partLabel"]["variableA"] == "single"
    assert define["partLabel"]["transforms"] == [{"id": "sp:transform:replace:string",
                                                   "input": {"pattern": "single", "replacement": ""}}]
    check = steps["Part Given?"]
    assert check["choiceList"][0]["variableA.$"] == "$.interactiveForm.formData.partLabel"
    assert check["choiceList"][0]["nextStep"] == "Set Part" and check["defaultStep"] == "Temporary?"
    assert steps["Set Part"]["nextStep"] == "Temporary?"
    assert steps["Set Part"]["attributes"]["variables"][0]["name"] == "$.defineVariableAccess.partLabel"
    regex = re.compile(check["choiceList"][0]["variableB"])
    for label in (rules.part_label(1, 3), rules.part_label(12, 12)):
        assert regex.match(label)
    for bad in ("", " (0/3)", "(1/3)", " (1/3) x", " (a/b)", " (1/3)" * 2):
        assert not regex.match(bad)
    assert "{{$.defineVariableAccess.partLabel}}" in steps["Email Requested"]["attributes"]["subject"]
    assert "{{$.defineVariableAccess.partLabel}}" in steps["Notify Requested"]["attributes"]["message"]


@pytest.mark.parametrize("remove_duration,fields", [
    ("", {"accessType": False, "duration": "", "durationUnit": []}),
    ("30d", {"accessType": True, "duration": "30", "durationUnit": ["d"]}),
    ("720h", {"accessType": True, "duration": "720", "durationUnit": ["h"]}),
    ("2w", {"accessType": True, "duration": "2", "durationUnit": ["w"]}),
    ("3M", {"accessType": True, "duration": "3", "durationUnit": ["M"]}),
])
def test_launcher_form_access_fields(remove_duration, fields):
    assert rules.launcher_form_access(remove_duration) == fields
    if remove_duration:   # the unit options of the form are the duration suffixes
        assert fields["durationUnit"][0] in config.DURATION_UNITS.values()


@pytest.mark.parametrize("bad", ["abc", "0d", "30", "30x", "-1d", None])
def test_launcher_form_access_refuses_anything_else(bad):
    with pytest.raises(ValueError):
        rules.launcher_form_access(bad)


def test_end_date_choice_maps_onto_the_launcher_form():
    now = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
    choice = rules.access_choice(cfg_with(), "endDate", end_date="2026-10-10", now=now, tz=timezone.utc)
    assert rules.launcher_form_access(choice.remove_duration) == {
        "accessType": True, "duration": str(rules.end_date_hours("2026-10-10", now=now, tz=timezone.utc)), "durationUnit": ["h"]}


def test_show_config_says_who_can_submit_from_the_plugin(tmp_path, capsys):
    out = _show(tmp_path, capsys)
    assert 'plugin.submit "direct": the plugin files the access requests itself' in out
    assert "holders of the role 'ACME Bulk Access Request - Submit Access' can submit" in out
    out = _show(tmp_path, capsys, plugin__submit="launcher")
    assert 'plugin.submit "launcher": the plugin launches' in out


# ── emails (what / why / what happens next, links, Velocity safety) ───────────
import velocity_lite  # noqa: E402  (renders a send-email step the way SailPoint does; checked live, CONTRACTS §10)

UI = "https://acme.identitynow.com"
LINKS = definitions.EmailLinks(UI, "pid-1")
HOSTILE = 'Costs $5 #if( ## $foo ${bar} #end\nline 2 "quoted" back\\slash <b>x</b>'


def _state(*, part="", access="Permanent", people=("p1", "p2", "p3"), inc="INC0012345",
           justification="Quarter-end audit", **form_extra):
    items = [{"id": "a", "type": "ACCESS_PROFILE", "name": "ACME Finance Read"},
             {"id": "e", "type": "ENTITLEMENT", "name": "ACME-AP-Clerk"}]
    return {"getRequester": {"attributes": {"displayName": "Rae Quester"}},
            "interactiveForm": {"formData": {"people": list(people), "items": items, "inc": inc,
                                             "justification": justification, **form_extra}},
            "defineVariableAccess": {"partLabel": part, "accessLabel": access}}


def _render_email(cfg, step, *, links=LINKS, state=None, **kw):
    wf = definitions.bulk_workflow(cfg, owner_id="o", form_id="f", links=links)
    return velocity_lite.render_step(_steps(wf)[step], state or _state(**kw))


def _email_steps(wf):
    return {n: s for n, s in _steps(wf).items() if s.get("actionId") == "sp:send-email"}


def test_ui_base_url_comes_from_the_api_host_unless_configured():
    cfg = config.load(EXAMPLE)
    assert config.ui_base_url(cfg, "https://acme.api.identitynow.com") == "https://acme.identitynow.com"
    assert config.ui_base_url(cfg, "https://acme.api.identitynow.com/") == "https://acme.identitynow.com"
    assert config.ui_base_url(cfg, "https://demo-1.api.identitynow-demo.com") == "https://demo-1.identitynow-demo.com"
    for unknown in (None, "", "https://acme.identitynow.com", "https://api.acme.com", "ftp://acme.api.identitynow.com"):
        assert config.ui_base_url(cfg, unknown) is None, unknown
    custom = cfg_with(notifications__uiBaseUrl="https://iam.acme.com/")
    assert custom.ui_base_url_override == "https://iam.acme.com"
    assert config.ui_base_url(custom, "https://acme.api.identitynow.com") == "https://iam.acme.com"


@pytest.mark.parametrize("key,value,message", [
    ("notifications__uiBaseUrl", "acme.identitynow.com", config.MSG_UI_BASE_URL),
    ("notifications__uiBaseUrl", "https://acme.identitynow.com/#x", config.MSG_UI_BASE_URL),
    ("notifications__uiBaseUrl", "https://acme.identitynow.com/$x", config.MSG_UI_BASE_URL),
    ("notifications__uiBaseUrl", 42, config.MSG_UI_BASE_URL),
    ("notifications__helpContact", "x" * (config.HELP_CONTACT_MAX + 1), config.MSG_HELP_CONTACT),
    ("notifications__helpContact", ["a"], config.MSG_HELP_CONTACT),
])
def test_notification_settings_are_checked(key, value, message):
    with pytest.raises(config.ConfigError) as err:
        cfg_with(**{key: value})
    assert str(err.value) == message


def test_notification_defaults():
    cfg = config.load(EXAMPLE)
    assert cfg.help_contact is None and cfg.ui_base_url_override is None
    assert cfg_with(notifications__helpContact="  Service   Desk  ").help_contact == "Service Desk"


@pytest.mark.parametrize("mode", ["dry-run", "live"])
def test_every_email_says_what_why_and_where_to_go(mode):
    cfg = cfg_with(mode=mode)
    steps = _email_steps(definitions.bulk_workflow(cfg, owner_id="o", form_id="f", links=LINKS))
    assert set(steps) == {"Email Requested", "Email Reject Bad INC", "Email Reject Bad Duration", "Email Reject Bad Unit"}
    for name in steps:
        subject, body = _render_email(cfg, name)
        headings = ["What", "Why", "Need help?"]
        headings += ["What to fix", "What to do now"] if "Reject" in name else ["What happens next"]
        for heading in headings:
            assert f">{heading}</td>" in body, (name, heading)
        for text in ("INC0012345", "ACME Finance Read", "(access profile)", "ACME-AP-Clerk", "(entitlement)", "3 people",
                     "Quarter-end audit", "Rae Quester", "Contact your SailPoint administrator."):
            assert text in body, (name, text)
        # Nothing unrendered or empty-looking reaches the reader, and nothing mentions an approver of the request.
        for leftover in ("None", "$!{", "{{", "null", "undefined", "  (", "for approval", "Approver"):
            assert leftover not in body and leftover not in subject, (name, leftover)
        assert ("DRY RUN" in body) == (mode == "dry-run")


def test_the_requested_email_says_where_the_requests_are():
    _, body = _render_email(cfg_with(mode="live"), "Email Requested", access="Temporary: 30d")
    assert "Access was requested for every person and item on your bulk access request <b>INC0012345</b>." in body
    assert "don't appear in your Request Center → My Requests" in body
    assert "An administrator finds them all by searching SailPoint for the INC number." in body
    assert "Temporary access is removed automatically when it ends" in body
    _, body = _render_email(cfg_with(mode="live"), "Email Requested")
    assert "Each person keeps the access until it's removed." in body


@pytest.mark.parametrize("step,field,problem,state_kw,shown", [
    ("Reject Bad INC", "ServiceNow incident (INC) number", "Enter a ServiceNow incident number", {"inc": "INC12"}, "INC12"),
    ("Reject Bad Duration", "Duration", rules.MSG_DURATION_NUMBER, {"duration": "0"}, "0"),
    ("Reject Bad Unit", "Unit", rules.MSG_DURATION_UNIT, {"durationUnit": ["x"]}, "x"),
])
def test_rejection_emails_name_the_field_to_fix(step, field, problem, state_kw, shown):
    subject, body = _render_email(cfg_with(), f"Email {step}", **state_kw)
    assert subject.startswith("Not requested: ") and "INC0" not in subject
    assert f"Fix your bulk access request: {field}</td>" in body
    assert f"<b>{field}</b>" in body and problem in body
    assert "was stopped before anything was requested" in body and "Nothing was requested." in body
    assert f'<a href="{UI}/ui/d/launchpad"' in body
    assert f'<a href="{UI}/ui/plugin/pid-1"' in body and "(New request tab)" in body
    assert f'>You entered</td><td style="padding:4px 0;vertical-align:top">{shown}</td>' in body


def test_emails_without_links_still_name_every_page():
    for links in (definitions.EmailLinks(), definitions.EmailLinks(UI)):
        _, requested = _render_email(cfg_with(mode="live"), "Email Requested", links=links)
        _, rejected = _render_email(cfg_with(), "Email Reject Bad INC", links=links)
        assert "Approvals → Access Requests" in requested and "'ACME Bulk Access Request'" in requested
        assert "plugin/" not in requested + rejected                         # no plugin ID: no plugin link
        assert "<b>'ACME Bulk Access Request'</b> (New request tab)" in rejected
        if not links.ui:
            assert 'href="http' not in requested + rejected
    # Without the plugin deployment, no plugin pages are mentioned at all.
    _, body = _render_email(cfg_with(deployments__plugin=False, mode="live"), "Email Requested")
    assert "Approvals tab" not in body and "plugin/" not in body
    _, body = _render_email(cfg_with(deployments__plugin=False), "Email Reject Bad INC")
    assert "(New request tab)" not in body and "Launchpad" in body


def test_email_bodies_are_constant_velocity_and_values_travel_in_the_context():
    for mode in ("dry-run", "live"):
        wf = definitions.bulk_workflow(cfg_with(mode=mode, temporaryAccess__maxDays=30), owner_id="o", form_id="f",
                                       links=LINKS)
        for name, step in _email_steps(wf).items():
            attrs = step["attributes"]
            body = attrs["body"]
            # "##" is a Velocity comment; "{{…}}" would splice run data into the template.
            assert "##" not in body and "{{" not in body, name
            # Every "$" is a "$!{…}" reference or a directive's variable, every "#" a directive.
            stripped = re.sub(r"\$!\{[\w.]+\}", "", body)
            stripped = re.sub(r"#\{?(?:if|elseif|foreach|set|else|end)\b\}?", "", stripped)
            assert "#" not in stripped, name
            assert all(re.match(r"\$\w", stripped[i:i + 2]) for i, c in enumerate(stripped) if c == "$"), name
            # Context values are JSONPath or plain text, never templates: a template value containing a newline,
            # a quote or a backslash fails the send (verified live).
            assert not any("{{" in str(v) for v in attrs["context"].values()), name
            assert all(isinstance(v, str) for v in attrs["context"].values()), name


def test_requester_text_is_shown_verbatim_and_never_read_as_a_template():
    for step in ("Email Requested", "Email Reject Bad INC"):
        _, body = _render_email(cfg_with(mode="live"), step, justification=HOSTILE)
        assert f'<span style="white-space:pre-wrap">{HOSTILE}</span>' in body


def test_one_item_lists_arrive_unwrapped_and_still_render():
    # The workflow engine unwraps one-item lists: one person, and one item.
    _, body = _render_email(cfg_with(), "Email Requested", people=("p1",))
    assert "1 person" in body
    state = _state()
    state["interactiveForm"]["formData"]["items"] = state["interactiveForm"]["formData"]["items"][0]
    _, body = _render_email(cfg_with(), "Email Requested", state=state)
    assert "ACME Finance Read" in body and "ACME-AP-Clerk" not in body


def test_help_contact_is_escaped_and_linked():
    cfg = cfg_with(notifications__helpContact="IAM <desk>: iam@acme.com or https://help.acme.com/iam.")
    assert definitions.help_html(cfg) == ('IAM &lt;desk&gt;: <a href="mailto:iam@acme.com">iam@acme.com</a> or '
                                          '<a href="https://help.acme.com/iam">https://help.acme.com/iam</a>.')
    assert definitions.help_html(config.load(EXAMPLE)) == definitions.DEFAULT_HELP
    _, body = _render_email(cfg, "Email Requested")
    assert '<a href="mailto:iam@acme.com">iam@acme.com</a>' in body


def test_who_gets_which_email():
    steps = _email_steps(definitions.bulk_workflow(config.load(EXAMPLE), owner_id="o", form_id="f"))
    for attrs in (s["attributes"] for s in steps.values()):
        assert attrs["recipientEmailList.$"] == "$.getRequester.attributes.email" and "carbonCopy.$" not in attrs



@pytest.mark.parametrize("overrides", [{}, {"mode": "live"}, {"mode": "live", "temporaryAccess__maxDays": 90},
                                       {"temporaryAccess__enabled": False}])
def test_every_launcher_step_explains_itself(overrides):
    wf = definitions.bulk_workflow(cfg_with(**overrides), owner_id="o", form_id="f")

    def walk(steps):
        for name, step in steps.items():
            yield name, step
            if step.get("actionId") == "sp:loop:iterator":
                yield from walk(step["attributes"]["steps"])
    for name, step in walk(wf["definition"]["steps"]):
        assert (step.get("description") or "").strip(), f"{name} has no description"
        assert "{{" not in step["description"]
