"""Core unit tests: config validation, rules, and the generated definitions. No tenant needed."""

import importlib.util
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from bulkaccess import config, definitions, rules

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
    assert cfg.plugin_workflow_name == "ACME Bulk Access Request (Plugin)"


@pytest.mark.parametrize("key,value,fragment", [
    ("prefix", "", "prefix"),
    ("mode", "yolo", "mode"),
    ("inc__pattern", "([", "regular expression"),
    ("inc__example", "CHG123", "does not match"),
    ("catalog__types", ["GROUP"], "catalog.types"),
    ("catalog__maxItems", 26, "maxItems"),
    ("approval__timeoutDays", 0, "timeoutDays"),
    ("notifications__overrideRecipients", ["not-an-email"], "email"),
    ("plugin__alias", "Has Spaces", "alias"),
])
def test_bad_config_is_rejected_with_a_useful_message(key, value, fragment):
    with pytest.raises(config.ConfigError, match=fragment):
        cfg_with(**{key: value})


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


def test_validate_request_lists_every_problem():
    cfg = config.load(EXAMPLE)
    problems = rules.validate_request(cfg, requester_id="me", approver_id="me", people=[], items=[], inc="nope")
    assert "Choose at least one person." in problems
    assert "Choose at least one access item." in problems
    assert "The approver must be someone other than you." in problems
    assert cfg.inc_message in problems
    ok = rules.validate_request(cfg, requester_id="me", approver_id="boss", people=["p1"],
                                items=[{"id": "a", "type": "ACCESS_PROFILE"}], inc="INC0012345")
    assert ok == []


def test_the_approver_cannot_be_one_of_the_people():
    cfg = config.load(EXAMPLE)
    problems = rules.validate_request(cfg, requester_id="me", approver_id="boss", people=["p1", "boss"],
                                      items=[{"id": "a", "type": "ACCESS_PROFILE"}], inc="INC0012345")
    assert problems == ["The approver can't be one of the people getting the access."] == [rules.MSG_APPROVER_IN_PEOPLE]


def test_both_workflows_stop_when_the_approver_is_one_of_the_people():
    for variant, kw in (("launcher", {"form_id": "f"}), ("plugin", {})):
        steps = _steps(definitions.bulk_workflow(config.load(EXAMPLE), variant=variant, owner_id="o", **kw))
        assert steps["Approver Is Requester?"]["defaultStep"] == "Approver In People?"
        check = steps["Approver In People?"]
        people = "$.trigger.people" if variant == "plugin" else "$.interactiveForm.formData.people"
        # A JSONPath filter against the looked-up approver id (StringContains can't search a list;
        # the Launcher form's one-item approver list never matches inside a filter; verified live).
        assert [(c["variableA.$"], c["variableB.$"]) for c in check["choiceList"]] == \
            [(f"{people}[?(@ == $.getApprover.id)]", "$.getApprover.id")]
        assert all(c["comparator"] == "StringEquals" and c["nextStep"] == "Reject Approver In People"
                   for c in check["choiceList"])
        assert check["defaultStep"] == "INC Valid?"
        assert steps["Reject Approver In People"]["nextStep"] == "End Step - Rejected"


def test_catalog_options_carry_full_access_objects_and_respect_filters():
    cfg = cfg_with(catalog__types=["ACCESS_PROFILE"], catalog__nameStartsWith="ACME")
    opts = rules.catalog_options(cfg, [
        {"id": "1", "type": "ACCESS_PROFILE", "name": "ACME Bulk Test Access", "source": {"name": "ACME SaaS"}},
        {"id": "2", "type": "ACCESS_PROFILE", "name": "Sales Regional - AD"},
        {"id": "3", "type": "ROLE", "name": "ACME Role"},
    ])
    assert opts == [{"label": "ACME Bulk Test Access", "subLabel": "Access profile · ACME SaaS",
                     "value": {"id": "1", "type": "ACCESS_PROFILE", "name": "ACME Bulk Test Access"}}]


# ── definitions ───────────────────────────────────────────────────────────────
def _form_elements(form):
    return {e["key"]: e for e in form["formElements"][0]["config"]["formElements"]}


def test_form_enforces_inc_regex_and_required_fields():
    cfg = config.load(EXAMPLE)
    els = _form_elements(definitions.bulk_form(cfg, "owner", []))
    assert {"people", "items", "approver", "inc", "justification"} <= set(els)
    regex = next(v for v in els["inc"]["validations"] if v["validationType"] == "REGEX")
    assert regex["config"] == {"regex": cfg.inc_pattern, "message": cfg.inc_message}   # shape SailPoint accepts
    assert els["approver"]["config"]["maximum"] == 1
    assert els["items"]["config"]["dataSource"]["dataSourceType"] == "STATIC"


def test_show_config_recommends_a_service_identity_when_owner_is_null(tmp_path, capsys):
    out = _show(tmp_path, capsys)
    assert f"Note:        {config.OWNER_NOTE}" in out
    assert "Consider a dedicated service identity in `owner`." in config.OWNER_NOTE
    out = _show(tmp_path, capsys, owner="2c9180835d2e5168015d32f890ca1581")
    assert "Owner:       2c9180835d2e5168015d32f890ca1581" in out and config.OWNER_NOTE not in out


def _approved_body(cfg, variant="plugin"):
    return _steps(definitions.bulk_workflow(cfg, variant=variant, owner_id="o", form_id="f"))["Email Approved"]["attributes"]["body"]


def test_approved_email_says_item_approvals_still_apply_and_points_to_the_approvals_tab():
    for variant in ("launcher", "plugin"):
        body = _approved_body(cfg_with(mode="live"), variant)
        assert "still need that approval in SailPoint for each person" in body
        assert "on the Approvals tab of 'ACME Bulk Access Request'" in body
    # No Approvals tab: the sentence stays, the pointer goes.
    for overrides in ({"approvals__enabled": False}, {"deployments__plugin": False}):
        body = _approved_body(cfg_with(mode="live", **overrides), "launcher")
        assert "still need that approval" in body and "Approvals tab" not in body
    # Dry run requests nothing, so there is nothing for item approvers to decide.
    assert "still need that approval" not in _approved_body(cfg_with())
    assert "still need" not in json.dumps(_steps(definitions.bulk_workflow(cfg_with(mode="live"), variant="plugin",
                                                                           owner_id="o"))["Email Denied"])


def test_bulk_approval_description_stays_short_enough():
    # The item-approval hint is left out of the description: it must leave room for the templated name and label.
    desc = _steps(definitions.bulk_workflow(cfg_with(mode="live"), variant="plugin", owner_id="o"))["Bulk Approval"]["attributes"]["description"]
    assert "Approvals tab" not in desc


def _steps(wf):
    return wf["definition"]["steps"]


def test_dry_run_never_requests_access_and_live_does():
    dry = definitions.bulk_workflow(config.load(EXAMPLE), variant="launcher", owner_id="o", form_id="f")
    assert "Request Access" not in _steps(dry)
    assert "DRY RUN" in json.dumps(_steps(dry)["Email Approved"])
    live = definitions.bulk_workflow(cfg_with(mode="live"), variant="launcher", owner_id="o", form_id="f")
    loop = _steps(live)["Request Access"]["attributes"]
    assert loop["input.$"] == "$.interactiveForm.formData.people"      # one iteration per person
    assert loop["context.$"] == "$"                                    # steps in a loop only see $.loop.*
    manage = loop["steps"]["Manage Access"]["attributes"]
    assert manage["addIdentities.$"] == "$.loop.loopInput"
    assert manage["requestedItems.$"] == "$.loop.context.interactiveForm.formData.items"   # all items per request
    assert "{{$.loop.context.interactiveForm.formData.inc}}" in manage["comments"]


def test_approval_goes_to_the_chosen_approver_and_branches_on_status():
    for variant, approver_path in (("launcher", "$.interactiveForm.formData.approver"), ("plugin", "$.trigger.approverId")):
        wf = definitions.bulk_workflow(cfg_with(mode="live"), variant=variant, owner_id="o", form_id="f")
        approval = _steps(wf)["Bulk Approval"]["attributes"]
        assert approval["approvalType"] == "SINGLE" and approval["singleApproverCategory"] == "IDENTITY"
        assert approval["singleApproverIdentityId.$"] == approver_path
        choice = _steps(wf)["Approved?"]["choiceList"][0]
        assert choice["variableA.$"] == "$.bulkApproval.status" and choice["nextStep"] == "Request Access"
        assert _steps(wf)["Approved?"]["defaultStep"] == "Email Denied"


def test_launcher_trigger_is_scoped_to_its_own_workflow():
    wf = definitions.bulk_workflow(config.load(EXAMPLE), variant="launcher", owner_id="o", form_id="f", workflow_id="w-123")
    assert wf["trigger"]["attributes"]["id"] == "idn:interactive-process-launched"
    assert wf["trigger"]["attributes"]["filter.$"] == "$[?(@.workflowId == 'w-123')]"


def test_plugin_variant_is_external_and_disabled():
    wf = definitions.bulk_workflow(config.load(EXAMPLE), variant="plugin", owner_id="o")
    assert wf["trigger"]["type"] == "EXTERNAL" and wf["enabled"] is False
    assert "Interactive Form" not in _steps(wf)


def test_demo_tenants_can_redirect_all_email():
    cfg = cfg_with(notifications__overrideRecipients=["demo@example.com"])
    wf = definitions.bulk_workflow(cfg, variant="launcher", owner_id="o", form_id="f")
    for name in ("Email Approved", "Email Denied"):
        attrs = _steps(wf)[name]["attributes"]
        assert attrs["recipientEmailList"] == ["demo@example.com"] and "carbonCopy.$" not in attrs


def test_every_next_step_exists():
    wf = definitions.bulk_workflow(cfg_with(mode="live"), variant="launcher", owner_id="o", form_id="f")
    steps = _steps(wf)
    targets = {s.get("nextStep") for s in steps.values()} | {s.get("defaultStep") for s in steps.values()}
    targets |= {c["nextStep"] for s in steps.values() for c in s.get("choiceList", [])}
    assert {t for t in targets if t} <= set(steps)
    assert wf["definition"]["start"] in steps


def test_another_tenant_gets_its_own_names():
    other = cfg_with(prefix="ACME", plugin__alias="acme-bulk")
    assert definitions.bulk_form(other, "o", [])["name"] == "ACME Bulk Access Request Form"
    assert definitions.bulk_workflow(other, variant="plugin", owner_id="o")["name"] == "ACME Bulk Access Request (Plugin)"
    assert definitions.bulk_launcher(other, "w")["name"] == "ACME Bulk Access Request"


# ── lessons from the live tenant ──────────────────────────────────────────────
def test_form_people_picker_is_capped_at_sailpoints_30_selection_limit():
    cfg = cfg_with(people__max=100)
    els = _form_elements(definitions.bulk_form(cfg, "o", []))
    assert els["people"]["config"]["maximum"] == definitions.FORM_SELECT_MAX == 30


def test_catalog_never_offers_the_profile_that_grants_the_tool_itself():
    cfg = cfg_with(catalog__nameStartsWith="ACME")
    opts = rules.catalog_options(cfg, [
        {"id": "1", "type": "ACCESS_PROFILE", "name": "ACME Bulk Access Request - Launcher Access"},
        {"id": "2", "type": "ACCESS_PROFILE", "name": "ACME Bulk Test Access"},
    ])
    assert [o["label"] for o in opts] == ["ACME Bulk Test Access"]


def test_launcher_access_profile_wraps_the_assigned_launchers_entitlement():
    ent = {"id": "e1", "name": "ACME Bulk Access Request", "source": {"id": "s1", "name": "IdentityNow"}}
    ap = definitions.launcher_access_profile(config.load(EXAMPLE), "o", ent)
    assert ap["name"] == "ACME Bulk Access Request - Launcher Access"
    assert ap["requestable"] is True and ap["entitlements"] == [{"id": "e1", "type": "ENTITLEMENT", "name": "ACME Bulk Access Request"}]
    assert ap["source"]["id"] == "s1"
    assert ap["accessRequestConfig"]["approvalSchemes"] == [{"approverType": "MANAGER"}]
    no_approval = definitions.launcher_access_profile(cfg_with(access__launcherApproval="NONE"), "o", ent)
    assert no_approval["accessRequestConfig"]["approvalSchemes"] == []


def test_failure_end_step_has_the_fields_the_validator_requires():
    wf = definitions.bulk_workflow(config.load(EXAMPLE), variant="launcher", owner_id="o", form_id="f")
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


def test_plugin_regex_allows_hours_for_end_dates_and_empty_for_permanent():
    cfg = cfg_with(temporaryAccess__units=["DAYS"], temporaryAccess__maxDays=30)
    pattern = re.compile(definitions.plugin_duration_regex(cfg))
    assert pattern.match("") and pattern.match("30d") and pattern.match("720h")
    assert not pattern.match("31d") and not pattern.match("721h") and not pattern.match("abc") and not pattern.match("1w")
    off = re.compile(definitions.plugin_duration_regex(cfg_with(temporaryAccess__enabled=False)))
    assert off.match("") and not off.match("1d")


# ── definitions: temporary access and parts ───────────────────────────────────
LIVE = {"mode": "live"}


def _manage(wf):
    return _steps(wf)["Request Access"]["attributes"]["steps"]["Manage Access"]


def test_manage_access_is_version_2_with_remove_duration_in_both_variants():
    cfg = cfg_with(**LIVE)
    plugin = _manage(definitions.bulk_workflow(cfg, variant="plugin", owner_id="o"))
    assert plugin["versionNumber"] == 2
    assert plugin["attributes"]["removeDuration.$"] == "$.loop.context.trigger.removeDuration"
    launcher = _manage(definitions.bulk_workflow(cfg, variant="launcher", owner_id="o", form_id="f"))
    assert launcher["versionNumber"] == 2
    assert launcher["attributes"]["removeDuration.$"] == "$.loop.context.defineVariableAccess.removeDuration"
    # The Launcher still uses it when temporary access is off ("" = permanent, always set).
    off = _manage(definitions.bulk_workflow(cfg_with(mode="live", temporaryAccess__enabled=False),
                                            variant="launcher", owner_id="o", form_id="f"))
    assert off["attributes"]["removeDuration.$"] == "$.loop.context.defineVariableAccess.removeDuration"


def test_approval_name_and_description_carry_the_part_and_access_labels():
    cfg = config.load(EXAMPLE)
    plugin = _steps(definitions.bulk_workflow(cfg, variant="plugin", owner_id="o"))["Bulk Approval"]["attributes"]
    assert plugin["name"] == "Bulk access {{$.trigger.inc}}{{$.trigger.partLabel}}"
    assert plugin["description"] == ("ACME bulk access request {{$.trigger.inc}}{{$.trigger.partLabel}} from "
                                     "{{$.getRequester.attributes.displayName}} · {{$.trigger.accessLabel}}")
    launcher = _steps(definitions.bulk_workflow(cfg, variant="launcher", owner_id="o", form_id="f"))["Bulk Approval"]["attributes"]
    assert launcher["name"] == "Bulk access {{$.interactiveForm.formData.inc}}"           # always one part
    assert launcher["description"].endswith(" · {{$.defineVariableAccess.accessLabel}}")
    # Fixed text stays within SailPoint's 50-character name limit for the default INC format.
    assert len("Bulk access INC0012345 (10/10)") <= rules.APPROVAL_NAME_MAX


def test_item_comment_keeps_the_inc_first_and_adds_the_access_label():
    comment = _manage(definitions.bulk_workflow(cfg_with(**LIVE), variant="plugin", owner_id="o"))["attributes"]["comments"]
    assert comment.split(" | ") == [
        "{{$.loop.context.trigger.inc}}", "Bulk access request by {{$.loop.context.getRequester.attributes.displayName}}",
        "Approved by {{$.loop.context.getApprover.attributes.displayName}}", "{{$.loop.context.trigger.accessLabel}}",
        "{{$.loop.context.trigger.justification}}"]


def test_emails_say_which_part_and_the_access_label():
    steps = _steps(definitions.bulk_workflow(config.load(EXAMPLE), variant="plugin", owner_id="o"))
    for name in ("Email Approved", "Email Denied"):
        attrs = steps[name]["attributes"]
        assert "{{$.trigger.partLabel}}" in attrs["subject"] and "{{$.trigger.partLabel}}" in attrs["body"]
        assert "{{$.trigger.accessLabel}}" in attrs["body"]


def test_plugin_trigger_lists_every_input_field():
    wf = definitions.bulk_workflow(config.load(EXAMPLE), variant="plugin", owner_id="o")
    description = wf["trigger"]["attributes"]["description"]
    for field in definitions.PLUGIN_INPUT:
        assert field in description, field
    steps = _steps(wf)
    assert steps["INC Valid?"]["choiceList"][0]["nextStep"] == "Access Valid?"
    check = steps["Access Valid?"]
    assert check["choiceList"][0]["variableA.$"] == "$.trigger.removeDuration"
    assert check["choiceList"][0]["nextStep"] == "Bulk Approval" and check["defaultStep"] == "Reject Bad Duration"


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
        assert set(_form_elements(form)) == {"people", "items", "approver", "inc", "justification"}
        assert form["formConditions"] == []

    # A unit that can never fit maxDays isn't offered.
    short = _form_elements(definitions.bulk_form(cfg_with(temporaryAccess__maxDays=7), "o", []))
    assert [o["value"] for o in short["durationUnit"]["config"]["dataSource"]["config"]["options"]] == ["h", "d", "w"]


def test_launcher_checks_the_duration_before_the_approval():
    steps = _steps(definitions.bulk_workflow(cfg_with(temporaryAccess__maxDays=7), variant="launcher",
                                             owner_id="o", form_id="f"))
    assert steps["INC Valid?"]["choiceList"][0]["nextStep"] == "Define Variable Access"
    assert steps["Define Variable Access"]["type"] == "Mutation"     # "Define Variable" prefix: Update Variable needs it
    assert steps["Temporary?"]["actionId"] == "sp:compare-boolean"
    assert steps["Temporary?"]["choiceList"][0]["variableA.$"] == "$.interactiveForm.formData.accessType"
    assert steps["Temporary?"]["defaultStep"] == "Notify Pending"    # permanent goes straight on
    assert steps["Duration Valid?"]["choiceList"][0]["variableB"] == "^[1-9][0-9]*$"
    assert steps["Unit Valid?"]["choiceList"][0]["variableB"] == "^(?:h|d|w)$"
    updated = {v["name"] for v in steps["Set Temporary Access"]["attributes"]["variables"]}
    assert updated == {"$.defineVariableAccess.removeDuration", "$.defineVariableAccess.accessLabel"}
    limit = steps["Within Limit?"]["choiceList"][0]
    assert limit["variableA.$"] == "$.defineVariableAccess.removeDuration"
    assert re.match(limit["variableB"], "168h") and not re.match(limit["variableB"], "8d")
    messages = {name: steps[name]["attributes"]["message"] for name in ("Reject Bad Duration", "Reject Bad Unit", "Reject Too Long")}
    assert messages == {"Reject Bad Duration": "<p>Enter the duration as a whole number of 1 or more.</p>",
                        "Reject Bad Unit": "<p>Choose a unit for the duration.</p>",
                        "Reject Too Long": "<p>Temporary access can last at most 7 days.</p>"}
    # Every path to the approval passes the checks: nothing reaches "Bulk Approval" except via Notify Pending.
    into_approval = [n for n, s in steps.items() if s.get("nextStep") == "Bulk Approval"]
    assert into_approval == ["Notify Pending"]


def test_launcher_without_max_days_or_temporary_access_has_no_extra_checks():
    steps = _steps(definitions.bulk_workflow(config.load(EXAMPLE), variant="launcher", owner_id="o", form_id="f"))
    assert "Within Limit?" not in steps and steps["Set Temporary Access"]["nextStep"] == "Notify Pending"
    off = _steps(definitions.bulk_workflow(cfg_with(temporaryAccess__enabled=False), variant="launcher", owner_id="o", form_id="f"))
    assert "Temporary?" not in off and off["Define Variable Access"]["nextStep"] == "Notify Pending"


@pytest.mark.parametrize("overrides", [
    {}, {"mode": "live"}, {"mode": "live", "temporaryAccess__maxDays": 7}, {"temporaryAccess__enabled": False},
    {"mode": "live", "temporaryAccess__allow": ["endDate"]}, {"mode": "live", "temporaryAccess__units": ["DAYS"]},
])
def test_both_variants_are_complete_graphs(overrides):
    cfg = cfg_with(**overrides)
    for variant in definitions.VARIANTS:
        wf = definitions.bulk_workflow(cfg, variant=variant, owner_id="o", form_id="f", workflow_id="w")
        steps = _steps(wf)
        targets = {s.get("nextStep") for s in steps.values()} | {s.get("defaultStep") for s in steps.values()}
        targets |= {c["nextStep"] for s in steps.values() for c in s.get("choiceList", [])}
        assert {t for t in targets if t} <= set(steps), (variant, overrides)
        assert wf["definition"]["start"] in steps
        reachable, todo = set(), [wf["definition"]["start"]]
        while todo:
            name = todo.pop()
            if name in reachable:
                continue
            reachable.add(name)
            s = steps[name]
            todo += [t for t in [s.get("nextStep"), s.get("defaultStep"), *[c["nextStep"] for c in s.get("choiceList", [])]] if t]
        assert reachable == set(steps), (variant, overrides, set(steps) - reachable)
        for s in steps.values():
            if s.get("type") == "failure":
                assert s["failureName"] and s["description"]


# ── config compatibility ──────────────────────────────────────────────────────
def test_old_launcher_access_approval_key_still_works_with_a_note():
    data = json.loads(EXAMPLE.read_text())
    del data["access"]
    data["launcher"] = {"accessApproval": "NONE"}
    cfg = config.from_dict(data)
    assert cfg.launcher_access_approval == "NONE"
    assert any("launcher.accessApproval" in d and "access.launcherApproval" in d for d in cfg.deprecations)
    data["access"] = {"launcherApproval": "MANAGER"}                         # the new key wins
    assert config.from_dict(data).launcher_access_approval == "MANAGER"
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
    assert "People:      no limit, sent as approvals of up to 250 (plugin) · up to 30 (Launcher form, SailPoint limit)" in out
    assert "  launcher:  duration in hours, days, weeks, months (no end date" in out
    assert "  plugin:    duration in hours, days, weeks, months or end date (sent as hours)" in out
    assert "Deployments: launcher, plugin\n" in out and "Deprecated" not in out


def test_show_config_launcher_only(tmp_path, capsys):
    out = _show(tmp_path, capsys, deployments__plugin=False, people__max=10, launcher={"accessApproval": "NONE"})
    assert "People:      up to 10 (Launcher form)" in out
    assert "(plugin off)" in out and "  plugin:" not in out
    assert "Deprecated:  `launcher.accessApproval` is now `access.launcherApproval`." in out


def test_show_config_plugin_only(tmp_path, capsys):
    out = _show(tmp_path, capsys, deployments__launcher=False, people__max=600, people__partSize=200,
                temporaryAccess__maxDays=14, temporaryAccess__allow=["endDate"])
    assert "People:      up to 600, sent as approvals of up to 200 (plugin)" in out
    assert "Launcher form" not in out and "(launcher off)" in out
    assert "Temporary:   at most 14 days" in out
    assert "  plugin:    end date (sent as hours)" in out and "  launcher:" not in out


def test_apply_runs_only_enabled_deployments_with_the_right_flags(tmp_path, monkeypatch):
    cli = _cli()
    calls = []
    monkeypatch.setattr(cli, "run_script", lambda d, script, argv, **k: calls.append((d, script, argv)) or 0)
    both = _config_file(tmp_path, plugin__public=True)
    assert cli.main(["apply", "--config", both, "--dry-run", "--grant", "me"]) == 0
    assert calls == [("launcher", "install", ["--config", both, "--dry-run", "--grant", "me"]),
                     ("plugin", "install", ["--config", both, "--dry-run", "--public"])]
    calls.clear()
    private = _config_file(tmp_path)
    assert cli.main(["apply", "--config", private, "--only", "plugin", "--deploy"]) == 0
    assert calls == [("plugin", "install", ["--config", private, "--deploy"])]
    calls.clear()
    launcher_only = _config_file(tmp_path, deployments__plugin=False)
    assert cli.main(["status", "--config", launcher_only]) == 0
    assert calls == [("launcher", "status", ["--config", launcher_only])]
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


def test_cli_loads_both_folders_scripts_without_mixing_them_up():
    cli = _cli()
    with cli._script_dir(ROOT / "launcher"):
        launcher_install = cli.load_script("launcher", "install")
    with cli._script_dir(ROOT / "plugin"):
        plugin_install = cli.load_script("plugin", "install")
    assert hasattr(launcher_install, "requestable_objects") and not hasattr(plugin_install, "requestable_objects")
    assert Path(plugin_install.__file__).parent.name == "plugin"


def test_export_offline_builds_every_object_with_placeholders(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("bulkaccess_cli", ROOT / "bulkaccess.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    assert cli.main(["export", "--offline", "--config", str(EXAMPLE), "--out", str(tmp_path)]) == 0
    files = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*.json"))
    assert files == ["launcher/access-profile.json", "launcher/form.json", "launcher/launcher.json",
                     "launcher/workflow.json", "plugin/workflow.json"]
    wf = json.loads((tmp_path / "launcher/workflow.json").read_text())
    assert wf["definition"]["steps"]["Interactive Form"]["attributes"]["formDefinitionId"] == "<FORM_DEFINITION_ID>"
    assert wf["trigger"]["attributes"]["filter.$"] == "$[?(@.workflowId == '<WORKFLOW_ID>')]"
    cli.main(["export", "--offline", "--only", "plugin", "--config", str(EXAMPLE), "--out", str(tmp_path / "p")])
    assert [p.name for p in (tmp_path / "p").rglob("*.json")] == ["workflow.json"]


# ── bulk approvals: the item comment read back, and the `approvals` config ────
BULK = "INC0012345 | Bulk access request by Ada Lovelace | Approved by Grace Hopper | Temporary: 30 days | Quarterly audit"
PARSED = {"inc": "INC0012345", "requester": "Ada Lovelace", "approver": "Grace Hopper",
          "accessLabel": "Temporary: 30 days", "justification": "Quarterly audit"}


def _bulk(justification, label="Permanent"):
    return {"inc": "INC0012345", "requester": "Ada", "approver": "Grace", "accessLabel": label,
            "justification": justification}


def test_comment_separator_is_what_definitions_writes():
    assert rules.COMMENT_SEPARATOR == " | "


@pytest.mark.parametrize("text,expected", [
    (BULK, PARSED),
    # the justification is everything after the 4th separator, " | " included
    ("INC0012345 | Bulk access request by Ada | Approved by Grace | Permanent | move | to | finance",
     _bulk("move | to | finance")),
    # extra whitespace around the separators and inside the labels
    ("  INC0012345   |  Bulk  access request   by  Ada Lovelace |Approved   by Grace Hopper|  Temporary: 30 days  |"
     "  Quarterly audit  ", PARSED),
    # an empty justification (a trailing separator, with or without its space)
    ("INC0012345 | Bulk access request by Ada | Approved by Grace | Permanent | ", _bulk("")),
    ("INC0012345 | Bulk access request by Ada | Approved by Grace | Permanent |", _bulk("")),
    # a multi-line justification is kept as written
    ("INC0012345 | Bulk access request by Ada | Approved by Grace | Permanent | line one\nline two",
     _bulk("line one\nline two")),
])
def test_parse_bulk_comment_reads_the_bulk_item_comment(text, expected):
    assert rules.parse_bulk_comment(config.load(EXAMPLE), text) == expected


@pytest.mark.parametrize("text", [
    None, "", 42, "INC0012345", "INC0012345: Quarterly audit",          # plain comments, the INC alone
    "Please approve, thanks",
    "INC0012345 | Bulk access request by Ada | Approved by Grace | Permanent",          # only 4 fields
    "INC12345 | Bulk access request by Ada | Approved by Grace | Permanent | why",       # INC fails the pattern
    "CHG0012345 | Bulk access request by Ada | Approved by Grace | Permanent | why",
    "INC0012345 | Access request by Ada | Approved by Grace | Permanent | why",          # wrong labels
    "INC0012345 | Bulk access request by Ada | Rejected by Grace | Permanent | why",
    "INC0012345 | Approved by Grace | Bulk access request by Ada | Permanent | why",     # wrong order
    "INC0012345 | Bulk access request by | Approved by Grace | Permanent | why",          # empty names or label
    "INC0012345 | Bulk access request by Ada | Approved by  | Permanent | why",
    "INC0012345 | Bulk access request by Ada | Approved by Grace |  | why",
    "INC0012345 | Bulk access request byAda | Approved by Grace | Permanent | why",
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


@pytest.mark.parametrize("variant", ["plugin", "launcher"])
def test_the_comment_definitions_writes_parses_back_to_its_inputs(variant):
    cfg = cfg_with(**LIVE)
    template = _manage(definitions.bulk_workflow(cfg, variant=variant, owner_id="o", form_id="f"))["attributes"]["comments"]
    expected = {"inc": "INC0012345", "requester": "Ada Lovelace", "approver": "Grace Hopper",
                "accessLabel": "Temporary: until 2026-11-07", "justification": "Audit | see INC0099999: finance | Q4"}
    rendered = _render(template, {"getRequester": expected["requester"], "getApprover": expected["approver"],
                                  "accessLabel": expected["accessLabel"], "justification": expected["justification"],
                                  "inc": expected["inc"]})
    assert rendered.startswith("INC0012345 | Bulk access request by Ada Lovelace | ")
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
