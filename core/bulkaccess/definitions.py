"""Pure builders for the SailPoint objects both deployments install.

Nothing here calls the API: given a Config (and IDs the installer looked up),
each function returns the exact JSON body to send. That keeps the definitions
unit-testable and lets `install.py --dry-run` print what would be created.

Two workflow variants share one definition (`bulk_workflow`):

* "launcher" -- started by a Launcher in the Launchpad. An Interactive Form
  collects the request from whoever launched it; results are reported back as
  Interactive Messages and email.
* "plugin"   -- started by the Bulk Access UI plugin through the workflow test
  endpoint (a browser plugin cannot hold external-trigger secrets). The plugin
  passes the same fields as trigger input. Must stay DISABLED.

Everything below was confirmed against a live tenant (see INSTALL.md
"How it works"): REGEX validation shape, STATIC options carrying full access
objects, Generic Approval encoding, and Manage Access inside one loop over the
people (SailPoint rejects nested loops and caps a request at 10 recipients, so
one request per person -- each with all the chosen items -- has no limits).
"""

from __future__ import annotations

import json
from typing import Any

from .config import DURATION_UNITS, Config
from .rules import (APPROVAL_COMMENT_MAX, APPROVAL_DESCRIPTION_MAX, APPROVER_LABEL, COMMENT_SEPARATOR, MSG_APPROVER_IN_PEOPLE,
                    MSG_DURATION_NUMBER, MSG_DURATION_UNIT, REQUESTER_LABEL, duration_regex, msg_max_days, unit_max_count)

VARIANTS = ("launcher", "plugin")

from .config import FORM_SELECT_MAX  # noqa: E402  (re-exported; 30, SailPoint's form SELECT limit)

# Form field keys (also the plugin's trigger input names).
F_PEOPLE, F_ITEMS, F_APPROVER, F_INC, F_JUSTIFICATION = "people", "items", "approver", "inc", "justification"
# Launcher form: temporary access (only when the config offers durations on the Launcher).
F_ACCESS_TYPE, F_DURATION, F_DURATION_UNIT = "accessType", "duration", "durationUnit"
UNIT_OPTION_LABELS = {"HOURS": "Hours", "DAYS": "Days", "WEEKS": "Weeks", "MONTHS": "Months"}

# Plugin trigger input (CONTRACTS section 3); every field is always present.
PLUGIN_INPUT = ("people", "items", "approverId", "requesterId", "inc", "justification",
                "part", "parts", "partLabel", "removeDuration", "accessLabel")


def launcher_duration_units(cfg: Config) -> tuple[str, ...]:
    """Units the Launcher form offers: the configured ones that fit `maxDays` at least once
    (with maxDays 5, a week can never be chosen, so it isn't offered)."""
    if "duration" not in cfg.launcher_temporary_modes:
        return ()
    return tuple(u for u in cfg.temporary_units if unit_max_count(u, cfg.temporary_max_days) != 0)


def launcher_offers_temporary(cfg: Config) -> bool:
    return bool(launcher_duration_units(cfg))


def _owner(owner_id: str, owner_name: str | None = None) -> dict[str, Any]:
    return {"type": "IDENTITY", "id": owner_id, **({"name": owner_name} if owner_name else {})}


# ───────────────────────────────────────────────────────────────── form ──
def bulk_form(cfg: Config, owner_id: str, options: list[dict[str, Any]]) -> dict[str, Any]:
    """The intake form shown in the Launchpad."""
    required = [{"validationType": "REQUIRED"}]
    elements = [
        {"id": "people", "key": F_PEOPLE, "elementType": "SELECT", "validations": required,
         "config": {"label": "People who need the access", "maximum": cfg.launcher_people_cap, "forceSelect": True,
                    "helpText": f"Search and add up to {cfg.launcher_people_cap} people.",
                    "dataSource": {"dataSourceType": "INTERNAL", "config": {"objectType": "IDENTITY"}}}},
        {"id": "items", "key": F_ITEMS, "elementType": "SELECT", "validations": required,
         "config": {"label": "Access to request", "maximum": cfg.catalog_max_items, "forceSelect": True,
                    "helpText": "Items from the Request Center catalog. Everyone above gets every item chosen here.",
                    "dataSource": {"dataSourceType": "STATIC", "config": {"options": options}}}},
        {"id": "approver", "key": F_APPROVER, "elementType": "SELECT", "validations": required,
         "config": {"label": "Approver", "maximum": 1, "forceSelect": True,
                    "helpText": "One person approves or denies the whole request. It can't be you.",
                    "dataSource": {"dataSourceType": "INTERNAL", "config": {"objectType": "IDENTITY"}}}},
        {"id": "inc", "key": F_INC, "elementType": "TEXT",
         "validations": required + [{"validationType": "REGEX",
                                     "config": {"regex": cfg.inc_pattern, "message": cfg.inc_message}}],
         "config": {"label": "ServiceNow incident (INC) number", "placeholder": cfg.inc_example,
                    "helpText": cfg.inc_message}},
        # No MAX_LENGTH here: on a TEXTAREA it silently stops the submission from reaching the
        # workflow (verified live), and long justifications are accepted by the approval anyway.
        {"id": "justification", "key": F_JUSTIFICATION, "elementType": "TEXTAREA", "validations": required,
         "config": {"label": "Business justification", "rows": 3,
                    "helpText": "Shown to the approver and stored on every access request."}},
    ]
    conditions: list[dict[str, Any]] = []
    units = launcher_duration_units(cfg)
    if units:
        limit = f" Up to {cfg.temporary_max_days} days." if cfg.temporary_max_days else ""
        elements += [
            # A TOGGLE gives a real boolean (a SELECT may arrive as a one-item list).
            {"id": "accessType", "key": F_ACCESS_TYPE, "elementType": "TOGGLE", "validations": [],
             "config": {"label": "Access duration", "falseLabel": "Permanent", "trueLabel": "Temporary", "default": False,
                        "helpText": "Temporary access is removed automatically when the duration ends."}},
            # Not REQUIRED: hidden while Permanent. The workflow checks it again before the approval.
            {"id": "duration", "key": F_DURATION, "elementType": "TEXT",
             "validations": [{"validationType": "REGEX",
                              "config": {"regex": "^([1-9][0-9]*)?$",
                                         "message": "Enter the duration as a whole number of 1 or more."}}],
             "config": {"label": "Duration", "placeholder": "30",
                        "helpText": "How long the access lasts, as a whole number." + limit}},
            # Option values are Manage Access duration suffixes, so the workflow builds "30d" by concatenation.
            {"id": "durationUnit", "key": F_DURATION_UNIT, "elementType": "SELECT", "validations": [],
             "config": {"label": "Unit", "maximum": 1, "forceSelect": True,
                        "dataSource": {"dataSourceType": "STATIC", "config": {"options": [
                            {"label": UNIT_OPTION_LABELS[u], "value": DURATION_UNITS[u]} for u in units]}}}},
        ]
        # Same shape the form builder writes for "hide when a toggle is off".
        conditions = [{"ruleOperator": "AND",
                       "rules": [{"sourceType": "ELEMENT", "source": F_ACCESS_TYPE, "operator": "EQ",
                                  "valueType": "BOOLEAN", "value": "false"}],
                       "effects": [{"effectType": "HIDE", "config": {"element": "duration"}},
                                   {"effectType": "HIDE", "config": {"element": "durationUnit"}}]}]
    return {
        "name": cfg.form_name,
        "description": f"{cfg.prefix}: request access for several people at once, approved by one person, "
                       f"tracked by a ServiceNow INC number.",
        "owner": _owner(owner_id),
        "formInput": [],
        "formElements": [{"id": "section", "elementType": "SECTION",
                          "config": {"label": "Bulk access request", "formElements": elements}}],
        "formConditions": conditions,
    }


# ───────────────────────────────────────────────────────────── workflow ──
# The Launcher keeps its access choice in workflow variables. SailPoint only lets
# "Update Variable" change variables of a step whose name starts with "Define Variable"
# (verified live), and a variable can't be defined as a literal "" -- so it is defined as
# "permanent" and a replace transform empties it.
DEFINE_ACCESS = "Define Variable Access"
ACCESS_VARS = "$.defineVariableAccess"


def _paths(variant: str) -> dict[str, str]:
    if variant == "launcher":
        base = "$.interactiveForm.formData"
        return {"people": f"{base}.{F_PEOPLE}", "items": f"{base}.{F_ITEMS}", "approver": f"{base}.{F_APPROVER}",
                "inc": f"{base}.{F_INC}", "justification": f"{base}.{F_JUSTIFICATION}",
                "requester": "$.trigger.launchedBy.id",
                "accessType": f"{base}.{F_ACCESS_TYPE}", "duration": f"{base}.{F_DURATION}",
                "durationUnit": f"{base}.{F_DURATION_UNIT}",
                "removeDuration": f"{ACCESS_VARS}.removeDuration", "accessLabel": f"{ACCESS_VARS}.accessLabel"}
    if variant == "plugin":
        return {"people": "$.trigger.people", "items": "$.trigger.items", "approver": "$.trigger.approverId",
                "inc": "$.trigger.inc", "justification": "$.trigger.justification",
                "requester": "$.trigger.requesterId", "partLabel": "$.trigger.partLabel",
                "removeDuration": "$.trigger.removeDuration", "accessLabel": "$.trigger.accessLabel"}
    raise ValueError(f"variant must be one of {VARIANTS}")


def _t(path: str) -> str:
    """Template a JSONPath into a string attribute: "{{$.x}}"."""
    return "{{" + path + "}}"


def _success() -> dict[str, Any]:
    return {"actionId": "sp:operator-success", "type": "success", "displayName": ""}


def _failure() -> dict[str, Any]:
    # Failure end steps carry failureName/description at the top level (validator error e300 otherwise).
    return {"actionId": "sp:operator-failure", "type": "failure", "displayName": "",
            "failureName": "Bulk request rejected",
            "description": "Stopped before approval: the approver was the requester, or the INC number "
                           "or the temporary access duration was invalid."}


def _email(cfg: Config, subject: str, body: str, *, cc_approver: bool) -> dict[str, Any]:
    attrs: dict[str, Any] = {"subject": subject, "body": body, "context": {}}
    if cfg.override_recipients:
        attrs["recipientEmailList"] = list(cfg.override_recipients)   # demo/test tenants: never mail real people
    else:
        attrs["recipientEmailList.$"] = "$.getRequester.attributes.email"
        if cc_approver and cfg.cc_approver:
            attrs["carbonCopy.$"] = "$.getApprover.attributes.email"
    return {"actionId": "sp:send-email", "type": "action", "versionNumber": 2, "attributes": attrs}


def _choice(display: str, comparator: str, a: str, b: Any, yes: str, no: str, *,
            action: str = "sp:compare-strings") -> dict[str, Any]:
    return {"actionId": action, "type": "choice", "displayName": display,
            "choiceList": [{"comparator": comparator, "variableA.$": a, "variableB": b, "nextStep": yes}],
            "defaultStep": no}


def plugin_duration_regex(cfg: Config) -> str:
    """What the plugin may send as `removeDuration`: "" (permanent), a duration in the configured
    units, or hours (an end date is sent as hours), all within maxDays."""
    modes = cfg.plugin_temporary_modes
    units = list(cfg.temporary_units) if "duration" in modes else []
    if "endDate" in modes and "HOURS" not in units:
        units.insert(0, "HOURS")
    return duration_regex(units, cfg.temporary_max_days, allow_empty=True)


def bulk_workflow(cfg: Config, *, variant: str, owner_id: str, owner_name: str | None = None,
                  form_id: str | None = None, workflow_id: str | None = None) -> dict[str, Any]:
    """The bulk-request workflow. `workflow_id` (known after creation) scopes the launcher trigger."""
    p = _paths(variant)
    inc, who, appr = _t(p["inc"]), _t("$.getRequester.attributes.displayName"), _t("$.getApprover.attributes.displayName")
    part = _t(p["partLabel"]) if "partLabel" in p else ""     # the Launcher is always one part
    label = _t(p["accessLabel"])
    live = cfg.live
    mode_note = "" if live else " (DRY RUN: nothing was requested)"
    temporary_units = launcher_duration_units(cfg) if variant == "launcher" else ()

    steps: dict[str, Any] = {}
    start = "Get Requester"

    if variant == "launcher":
        if not form_id:
            raise ValueError("form_id is required for the launcher variant")
        start = "Interactive Form"
        steps["Interactive Form"] = {
            "actionId": "sp:interactive-form", "type": "action", "versionNumber": 1, "displayName": "Bulk request form",
            "attributes": {"formDefinitionId": form_id, "interactiveProcessId.$": "$.trigger.interactiveProcessId",
                           "ownerId.$": "$.trigger.launchedBy.id", "title": cfg.base_name,
                           "message": "<p>Request access for several people at once. One approver decides the "
                                      "whole request, and the ServiceNow INC number is recorded on every item.</p>"},
            "nextStep": "Get Requester"}

    steps["Get Requester"] = {"actionId": "sp:get-identity", "type": "action", "versionNumber": 2,
                              "displayName": "Requester", "attributes": {"id.$": p["requester"]}, "nextStep": "Get Approver"}
    steps["Get Approver"] = {"actionId": "sp:get-identity", "type": "action", "versionNumber": 2,
                             "displayName": "Approver", "attributes": {"id.$": p["approver"]}, "nextStep": "Approver Is Requester?"}

    # Defence in depth: the form/plugin already enforce all of these.
    steps["Approver Is Requester?"] = {
        "actionId": "sp:compare-strings", "type": "choice", "displayName": "Approver is the requester?",
        "choiceList": [{"comparator": "StringEquals", "variableA.$": p["approver"], "variableB.$": p["requester"],
                        "nextStep": "Reject Self Approval"}],
        "defaultStep": "Approver In People?"}
    # A JSONPath filter picks the approver out of the people list. It compares with the
    # looked-up approver's id (a plain string in both variants); comparing with the Launcher
    # form's one-item approver list never matches. StringContains can't search a list.
    steps["Approver In People?"] = {
        "actionId": "sp:compare-strings", "type": "choice", "displayName": "Approver is one of the people?",
        "choiceList": [{"comparator": "StringEquals", "variableA.$": f"{p['people']}[?(@ == $.getApprover.id)]",
                        "variableB.$": "$.getApprover.id", "nextStep": "Reject Approver In People"}],
        "defaultStep": "INC Valid?"}
    after_inc = DEFINE_ACCESS if variant == "launcher" else "Access Valid?"
    steps["INC Valid?"] = _choice("INC number valid?", "StringMatches", p["inc"], cfg.inc_pattern, after_inc, "Reject Bad INC")

    def stop(name: str, title: str, message: str) -> None:
        if variant == "launcher":
            steps[name] = {"actionId": "sp:interactive-message", "type": "action", "versionNumber": 1, "displayName": title,
                           "attributes": {"category": "ERROR", "interactiveProcessId.$": "$.trigger.interactiveProcessId",
                                          "ownerId.$": "$.trigger.launchedBy.id", "title": title, "message": f"<p>{message}</p>"},
                           "nextStep": "End Step - Rejected"}
        else:
            steps[name] = {**_email(cfg, f"{cfg.base_name}: {title}", f"<p>{message}</p>", cc_approver=False),
                           "displayName": title, "nextStep": "End Step - Rejected"}

    stop("Reject Self Approval", "Choose a different approver",
         "You can't approve your own bulk request. Start again and choose someone else as the approver.")
    stop("Reject Approver In People", "Choose a different approver",
         f"{MSG_APPROVER_IN_PEOPLE} Start again and choose an approver who isn't on the list.")
    stop("Reject Bad INC", "Invalid INC number", cfg.inc_message)

    if variant == "launcher":
        # The access choice: permanent ("" / "Permanent") unless the form asks for temporary access.
        steps[DEFINE_ACCESS] = {
            "attributes": {"id": "sp:define-variable", "variables": [
                {"name": "removeDuration", "description": "Manage Access removeDuration; empty means permanent",
                 "variableA": "permanent",
                 "transforms": [{"id": "sp:transform:replace:string", "input": {"pattern": "permanent", "replacement": ""}}]},
                {"name": "accessLabel", "description": "Shown on the approval, the access requests and the emails",
                 "variableA": "Permanent", "transforms": []}]},
            "type": "Mutation", "displayName": "Access: permanent unless temporary was chosen",
            "nextStep": "Temporary?" if temporary_units else "Notify Pending"}
        if temporary_units:
            suffixes = "|".join(DURATION_UNITS[u] for u in temporary_units)
            steps["Temporary?"] = _choice("Temporary access?", "BooleanEquals", p["accessType"], True,
                                          "Duration Valid?", "Notify Pending", action="sp:compare-boolean")
            steps["Duration Valid?"] = _choice("Duration a whole number?", "StringMatches", p["duration"], "^[1-9][0-9]*$",
                                               "Unit Valid?", "Reject Bad Duration")
            steps["Unit Valid?"] = _choice("Duration unit chosen?", "StringMatches", p["durationUnit"], f"^(?:{suffixes})$",
                                           "Set Temporary Access", "Reject Bad Unit")
            concat = lambda path: {"id": "sp:transform:concatenate:string", "input": {"variableB.$": path}}  # noqa: E731
            steps["Set Temporary Access"] = {
                "attributes": {"id": "sp:update-variable", "variables": [
                    {"name": f"{ACCESS_VARS}.removeDuration", "description": "", "variableA.$": p["duration"],
                     "transforms": [concat(p["durationUnit"])]},
                    {"name": f"{ACCESS_VARS}.accessLabel", "description": "", "variableA": "Temporary: ",
                     "transforms": [concat(p["duration"]), concat(p["durationUnit"])]}]},
                "type": "Mutation", "displayName": "Access: temporary",
                "nextStep": "Within Limit?" if cfg.temporary_max_days else "Notify Pending"}
            if cfg.temporary_max_days:
                steps["Within Limit?"] = _choice(f"At most {cfg.temporary_max_days} days?", "StringMatches",
                                                 p["removeDuration"], duration_regex(temporary_units, cfg.temporary_max_days),
                                                 "Notify Pending", "Reject Too Long")
                stop("Reject Too Long", "Duration too long", msg_max_days(cfg.temporary_max_days))
            stop("Reject Bad Duration", "Invalid duration", MSG_DURATION_NUMBER)
            stop("Reject Bad Unit", "Choose a unit", MSG_DURATION_UNIT)
    else:
        # The plugin validates the choice; an invalid removeDuration would only fail after approval.
        steps["Access Valid?"] = _choice("Temporary access valid?", "StringMatches", p["removeDuration"],
                                         plugin_duration_regex(cfg), "Bulk Approval", "Reject Bad Duration")
        stop("Reject Bad Duration", "Invalid temporary access",
             "The temporary access duration was not valid, so nothing was sent for approval.")

    if variant == "launcher":
        steps["Notify Pending"] = {
            "actionId": "sp:interactive-message", "type": "action", "versionNumber": 1, "displayName": "Submitted",
            "attributes": {"category": "INFO", "interactiveProcessId.$": "$.trigger.interactiveProcessId",
                           "ownerId.$": "$.trigger.launchedBy.id", "title": f"Sent to {appr} for approval",
                           "message": f"<p>Your bulk request <b>{inc}</b> ({label}) is waiting for {appr}. "
                                      f"You'll get an email when it's decided{mode_note}.</p>"},
            "nextStep": "Bulk Approval"}

    steps["Bulk Approval"] = {
        "actionId": "sp:generic-approval", "type": "action", "versionNumber": 1, "displayName": "One approval for the whole request",
        "attributes": {
            "name": f"Bulk access {inc}{part}",
            "description": f"{cfg.prefix} bulk access request {inc}{part} from {who} · {label}",
            "comments": f"{inc}: {_t(p['justification'])}",
            "requestedBy": "IDENTITY", "byIdentity.$": p["requester"],
            "requestedFor": "IDENTITY", "forIdentity.$": p["requester"],
            "approvalType": "SINGLE", "singleApproverCategory": "IDENTITY", "singleApproverIdentityId.$": p["approver"],
            "approvalReminder": "false", "approvalTimeout": cfg.approval_timeout_days,
            "approvalActionAtTimeout": cfg.approval_action_at_timeout, "priority": cfg.approval_priority},
        "nextStep": "Approved?"}
    steps["Approved?"] = _choice("Approved?", "StringEquals", "$.bulkApproval.status", "APPROVED",
                                 "Request Access" if live else "Email Approved", "Email Denied")

    if live:
        # One loop over the people; each iteration requests every chosen item for one
        # person. (No nested loops in SailPoint workflows, and max 10 recipients per request.)
        # Steps inside a loop only see $.loop.*, so the whole workflow state is passed in as
        # the loop context and the items / comment parts are read from $.loop.context.
        def in_loop(path: str) -> str:
            return "$.loop.context" + path[1:]
        # The plugin's Approvals tab reads this back with rules.parse_bulk_comment; keep them in step.
        loop_comment = COMMENT_SEPARATOR.join([
            _t(in_loop(p["inc"])),
            f"{REQUESTER_LABEL} {_t(in_loop('$.getRequester.attributes.displayName'))}",
            f"{APPROVER_LABEL} {_t(in_loop('$.getApprover.attributes.displayName'))}",
            _t(in_loop(p["accessLabel"])),
            _t(in_loop(p["justification"])),
        ])
        steps["Request Access"] = {
            "actionId": "sp:loop:iterator", "type": "action", "versionNumber": 1, "displayName": "Request access per person",
            "attributes": {"input.$": p["people"], "context.$": "$", "start": "Manage Access",
                           "steps": {"Manage Access": {
                               # v2 has removeDuration ("30d", "2h", ...); "" means permanent.
                               "actionId": "sp:access:manage", "type": "action", "versionNumber": 2,
                               "attributes": {"requestType": "GRANT_ACCESS", "addIdentities.$": "$.loop.loopInput",
                                              "requestedItems.$": in_loop(p["items"]),
                                              "removeDuration.$": in_loop(p["removeDuration"]),
                                              "comments": loop_comment},
                               "nextStep": "End Step - Success Item"},
                               "End Step - Success Item": {"type": "success"}}},
            "nextStep": "Email Approved"}

    steps["Email Approved"] = {
        **_email(cfg, f"Approved: bulk access {inc}{part}{mode_note}",
                 f"<p>Your bulk access request <b>{inc}</b>{part} was approved by {appr}.</p>"
                 f"<p>{'Access was requested for every person and item on the request.' if live else 'DRY RUN: this installation is in dry-run mode, so nothing was requested.'}</p>"
                 f"<p>Access: {label}</p>"
                 f"<p>Justification: {_t(p['justification'])}</p>", cc_approver=True),
        "displayName": "Email: approved", "nextStep": "End Step - Success"}
    steps["Email Denied"] = {
        **_email(cfg, f"Denied: bulk access {inc}{part}",
                 f"<p>Your bulk access request <b>{inc}</b>{part} was not approved by {appr} "
                 f"(status: {_t('$.bulkApproval.status')}). Nothing was requested.</p>"
                 f"<p>Access asked for: {label}</p>", cc_approver=True),
        "displayName": "Email: denied", "nextStep": "End Step - Success"}
    steps["End Step - Success"] = _success()
    steps["End Step - Rejected"] = _failure()

    if variant == "launcher":
        trigger: dict[str, Any] = {"type": "EVENT", "attributes": {"id": "idn:interactive-process-launched"}}
        if workflow_id:
            # The event fires for every launcher in the tenant; only react to our own.
            trigger["attributes"]["filter.$"] = f"$[?(@.workflowId == '{workflow_id}')]"
        description = (f"{cfg.prefix} Bulk Access Request ({cfg.mode}). Started by the '{cfg.launcher_name}' "
                       "Launcher. Installed by bulk-access-request/launcher/install.py.")
        name = cfg.launcher_workflow_name
    else:
        trigger = {"type": "EXTERNAL", "attributes": {
            "name": f"{cfg.plugin_alias}-workflow",
            "description": "Input: people[], items[], approverId, requesterId, inc, justification, part, parts, "
                           "partLabel, removeDuration (\"\" = permanent), accessLabel"}}
        description = (f"{cfg.prefix} Bulk Access Request for the UI plugin ({cfg.mode}). Started through the "
                       "workflow test endpoint, once per part of up to 250 people, so it must stay DISABLED. "
                       "Installed by bulk-access-request/plugin/install.py.")
        name = cfg.plugin_workflow_name

    return {"name": name, "description": description[:APPROVAL_DESCRIPTION_MAX * 3],
            "owner": _owner(owner_id, owner_name), "enabled": False,
            "definition": {"start": start, "steps": steps}, "trigger": trigger}


# ───────────────────────────────────────────────────────────── launcher ──
def bulk_launcher(cfg: Config, workflow_id: str) -> dict[str, Any]:
    return {
        "name": cfg.launcher_name,
        "description": "Request access for several people at once from the Request Center catalog. One approver "
                       "decides the whole request; a ServiceNow INC number is required and recorded on every item.",
        "type": "INTERACTIVE_PROCESS",
        "disabled": False,
        "reference": {"type": "WORKFLOW", "id": workflow_id},
        "config": "{}",
    }


def launcher_access_profile(cfg: Config, owner_id: str, entitlement: dict[str, Any]) -> dict[str, Any]:
    """Who may use the Launcher. SailPoint creates an `assignedLaunchers` entitlement on the
    built-in IdentityNow source for every Launcher; only identities holding it see it in the
    Launchpad. Wrapping it in a requestable access profile lets users ask for it in the
    Request Center (or admins grant it) like any other access."""
    source = entitlement.get("source") or {}
    return {
        "name": f"{cfg.base_name} - Launcher Access",
        "description": f"Lets the holder use the '{cfg.launcher_name}' Launcher in the Launchpad "
                       f"(bulk access requests with one approver and a ServiceNow INC number).",
        "owner": _owner(owner_id),
        "source": {"id": source.get("id"), "type": "SOURCE", "name": source.get("name")},
        "entitlements": [{"id": entitlement["id"], "type": "ENTITLEMENT", "name": entitlement.get("name")}],
        "enabled": True,
        "requestable": True,
        "accessRequestConfig": {"commentsRequired": False, "denialCommentsRequired": False,
                                "approvalSchemes": [{"approverType": "MANAGER"}] if cfg.launcher_access_approval == "MANAGER" else []},
    }


def pretty(obj: Any) -> str:
    return json.dumps(obj, indent=2, sort_keys=False)


__all__ = ["bulk_form", "bulk_workflow", "bulk_launcher", "VARIANTS", "APPROVAL_COMMENT_MAX", "PLUGIN_INPUT",
           "launcher_duration_units", "launcher_offers_temporary", "plugin_duration_regex"]
