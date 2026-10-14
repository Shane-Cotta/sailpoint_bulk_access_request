"""Pure builder for the "Reassign Ownership on Termination" workflow (no I/O; standard library only).

Flow (see README.md for the picture):
  trigger (lifecycle state changed → a termination state)
  → Get Departed Identity → Define Variable Owner (the fallback)
  → [manager-then-fallback] Has Manager? → Get Manager → Manager Inactive? → Use Manager
  → Owner Usable?  (not the departed identity itself, not an unfilled placeholder)
  → Get New Owner → New Owner Found? → New Owner Active?   (nothing changes for a missing or departed owner)
  → for each enabled object type: Get Owned X → Owns X? → Reassign X (loop: Patch X Owner)
  → [pendingApprovals] Get Pending Approvals → Has Pending Approvals? → Reassign Pending Approvals
    (loop: Reassign Approval, POST /generic-approvals/v1/{id}/reassign)
  → [pendingCertifications] Get Open Certifications → Has Open Certifications? → Reassign Open Certifications
    (loop: Reassign Certification, POST /campaigns/v1/{campaign}/reassign)
  → Recheck X … → [Recheck Pending Approvals] → [Recheck Open Certifications] → Anything Left? → [Alert Admins]
  → failure | success

Facts this relies on are listed, with how they were verified, in CLAUDE.md and docs/RESEARCH.md.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping

from . import api
from .config import Config

TRIGGER_ID = "idn:identity-lifecycle-state-changed"

# Placeholders in the exported JSON (workflow/*.json), replaced on import (README "Deploy without Python").
API_URL = "__API_URL__"
FALLBACK_OWNER_ID = "__FALLBACK_OWNER_ID__"
WORKFLOW_OWNER_ID = "__WORKFLOW_OWNER_ID__"
PARAMETER_ID = "__PARAMETER_ID__"
PLACEHOLDERS = (API_URL, FALLBACK_OWNER_ID, WORKFLOW_OWNER_ID, PARAMETER_ID)

# Parameter Storage type of an "OAuth 2.0 Client Credentials Grant" parameter, and how HTTP Request v3 maps its
# fields (from the action library's sp:http v3 form definition).
OAUTH_PARAMETER_TYPE = "1.4"
OAUTH_MAPPING = {"auth_client_id": "clientId", "auth_client_secret": "clientSecret",
                 "auth_credential_location": "credentialLocation", "auth_token_url": "tokenURL"}

# Step names. ISC exposes a step's output as $.<camelCase of its name>, so names are plain words
# (no acronyms), and the Define Variable step's name must start with "Define Variable".
GET_DEPARTED = "Get Departed Identity"
DEFINE_OWNER = "Define Variable Owner"
OWNER_VARS = "$.defineVariableOwner"
HAS_MANAGER = "Has Manager?"
GET_MANAGER = "Get Manager"
MANAGER_INACTIVE = "Manager Inactive?"
USE_MANAGER = "Use Manager"
OWNER_USABLE = "Owner Usable?"
GET_NEW_OWNER = "Get New Owner"
NEW_OWNER_FOUND = "New Owner Found?"
NEW_OWNER_ACTIVE = "New Owner Active?"
STOP_NO_OWNER = "Stop - No Usable Owner"
GET_APPROVALS = "Get Pending Approvals"
HAS_APPROVALS = "Has Pending Approvals?"
REASSIGN_APPROVALS = "Reassign Pending Approvals"
REASSIGN_APPROVAL = "Reassign Approval"
APPROVAL_REASSIGNED = "Approval Reassigned"
RECHECK_APPROVALS = "Recheck Pending Approvals"
GET_CERTS = "Get Open Certifications"
HAS_CERTS = "Has Open Certifications?"
REASSIGN_CERTS = "Reassign Open Certifications"
REASSIGN_CERT = "Reassign Certification"
CERT_REASSIGNED = "Certification Reassigned"
RECHECK_CERTS = "Recheck Open Certifications"
WAIT_CERTS = "Wait For Certifications"
# The campaign reassign is queued (ADMIN_REASSIGN, QUEUED): the certification still lists under the leaver for a few
# seconds (it had moved 10 s later, seen live), so the run waits before rechecking.
CERT_WAIT = "1m"           # the format ISC's own Wait steps use ("1m", "20m", "1h", "2d")
ANYTHING_LEFT = "Anything Left?"
ALERT_ADMINS = "Alert Admins"
STOP_LEFTOVERS = "Stop - Ownership Not Fully Reassigned"
DONE = "Done"

DEPARTED_ID = "$.trigger.identity.id"
NEW_OWNER_ID = f"{OWNER_VARS}.newOwnerId"

# A list call returns at most this many objects; the loop step's hard limit is 250 iterations.
PAGE = 250


# Inside a JSONPath filter, the departed identity is read from the run itself (verified live in a loop's input and
# in an IsPresent choice; a filter that matches nothing takes the choice's default branch).
OWNED_BY_DEPARTED = "[?(@.owner.id == " + DEPARTED_ID + ")]"


@dataclass(frozen=True)
class ObjectType:
    key: str                     # config objectTypes key
    plural: str                  # "Roles", used in step names
    singular: str                # "Role"
    path: str                    # list endpoint
    item_path: str               # GET/PATCH <item_path>/<id>
    server_filter: bool = True   # the list endpoint filters on owner.id; else list everything and filter here
    headers: str | None = None   # extra request headers, one "Key:Value" per line

    def step(self, verb: str) -> str:
        return f"{verb} {self.plural}"

    def output(self, verb: str) -> str:
        return "$." + _camel(self.step(verb))

    def owned(self, verb: str) -> str:
        """JSONPath to the departed identity's objects in the response of step `<verb> <plural>`."""
        return self.output(verb) + ".body" + ("" if self.server_filter else OWNED_BY_DEPARTED)

    def first_owned_id(self, verb: str) -> str:
        """JSONPath that's present only when that response holds at least one of their objects."""
        return self.owned(verb) + ("[0].id" if self.server_filter else ".id")


EXPERIMENTAL = "X-SailPoint-Experimental:true"
OBJECT_TYPES: dict[str, ObjectType] = {
    "roles": ObjectType("roles", "Roles", "Role", api.ROLES, api.ROLES),
    "accessProfiles": ObjectType("accessProfiles", "Access Profiles", "Access Profile",
                                 api.ACCESS_PROFILES, api.ACCESS_PROFILES),
    "entitlements": ObjectType("entitlements", "Entitlements", "Entitlement", api.ENTITLEMENTS, api.ENTITLEMENTS),
    "sources": ObjectType("sources", "Sources", "Source", api.SOURCES, api.SOURCES),
    # /source-apps lists only the caller's assigned apps; /source-apps/all is the admin list (it filters on owner.id).
    "apps": ObjectType("apps", "Apps", "App", f"{api.SOURCE_APPS}/all", api.SOURCE_APPS, headers=EXPERIMENTAL),
    # These two lists reject an owner.id filter (400): the workflow lists them and filters with JSONPath.
    "governanceGroups": ObjectType("governanceGroups", "Governance Groups", "Governance Group",
                                   api.WORKGROUPS, api.WORKGROUPS, server_filter=False),
    "identityProfiles": ObjectType("identityProfiles", "Identity Profiles", "Identity Profile",
                                   api.IDENTITY_PROFILES, api.IDENTITY_PROFILES, server_filter=False),
}


@dataclass(frozen=True)
class Target:
    """Where the workflow runs: real values from the installer, or the placeholders for export."""
    api_url: str = API_URL
    fallback_owner_id: str = FALLBACK_OWNER_ID
    workflow_owner_id: str = WORKFLOW_OWNER_ID
    workflow_owner_name: str | None = None
    parameter_id: str = PARAMETER_ID
    # Each HTTP step's link to the parameter (refID, by step name). ISC keeps a step's paramID only when the step
    # also carries a refID, and registers that reference itself (CLAUDE.md). Missing ones get ref_id()'s default.
    ref_ids: Mapping[str, str] = field(default_factory=dict)

    def ref_id(self, step: str) -> str:
        """The step's refID: the given one, else a stable UUID derived from the parameter and step name."""
        return self.ref_ids.get(step) or str(uuid.uuid5(uuid.NAMESPACE_URL, f"isc-ownership-reassignment:{self.parameter_id}:{step}"))


def _camel(name: str) -> str:
    words = "".join(c if c.isalnum() else " " for c in name).split()
    return words[0].lower() + "".join(w[:1].upper() + w[1:] for w in words[1:])


def trigger_filter(states: tuple[str, ...]) -> str:
    """JSONPath for the trigger: the new lifecycle state is one of `states` (technical names, case-sensitive)."""
    return "$[?(" + " || ".join(f'@.newLifecycleState == "{s}"' for s in states) + ")]"


def state_regex(states: tuple[str, ...]) -> str:
    return "^(?:" + "|".join(states) + ")$"


def _owner(owner_id: str, owner_name: str | None = None) -> dict[str, Any]:
    return {"type": "IDENTITY", "id": owner_id, **({"name": owner_name} if owner_name else {})}


def _choice(display: str, entries: list[dict[str, Any]], default: str, action: str | None = None) -> dict[str, Any]:
    step: dict[str, Any] = {"type": "choice", "displayName": display, "choiceList": entries, "defaultStep": default}
    if action:
        step["actionId"] = action
    return step


def _present(path: str, next_step: str) -> dict[str, Any]:
    """Choice entry: `path` exists. ISC's own "Verify Data Type" choice shape (no actionId)."""
    return {"comparator": "IsPresent", "variableA.$": path, "nextStep": next_step}


def _success() -> dict[str, Any]:
    return {"actionId": "sp:operator-success", "type": "success", "displayName": ""}


def _failure(name: str, description: str) -> dict[str, Any]:
    # Failure end steps need failureName/description at the top level (validator error e300 otherwise).
    return {"actionId": "sp:operator-failure", "type": "failure", "displayName": "",
            "failureName": name, "description": description}


def _http(t: Target, name: str, display: str, method: str, url: str, next_step: str | None, *,
          url_params: str | None = None, headers: str | None = None,
          patch: list[dict[str, Any]] | None = None, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """HTTP Request (sp:http v3) to the ISC API. It authenticates with a PAT kept in Parameter Storage (OAuth 2.0
    client credentials), so the workflow holds no secret: v2's inline secrets are dropped (top level) or stored
    readable (inside loops) when a workflow is created through the API (CLAUDE.md)."""
    attrs: dict[str, Any] = {
        "param_authenticationRef": "oauth",
        "param_oauth": {"mapping": dict(OAUTH_MAPPING), "paramID": t.parameter_id, "paramType": OAUTH_PARAMETER_TYPE,
                        "refID": t.ref_id(name)},
        "method": method,
        "url": url,
    }
    if url_params:
        attrs["urlParams"] = url_params   # one "key:value" per line
    if headers:
        attrs["requestHeaders"] = headers
    if patch is not None:
        attrs["requestContentType"] = "json-patch+json"
        attrs["jsonPatchRequestBody"] = patch
    else:
        attrs["requestContentType"] = "json"
        if body is not None:
            attrs["jsonRequestBody"] = body
    step = {"actionId": "sp:http", "type": "action", "versionNumber": 3, "displayName": display, "attributes": attrs}
    if next_step:
        step["nextStep"] = next_step
    return step


def _list_owned(t: Target, name: str, ot: ObjectType, verb: str, limit: int, next_step: str) -> dict[str, Any]:
    if ot.server_filter:
        params, display = (f'filters:owner.id eq "{{{{{DEPARTED_ID}}}}}"\nlimit:{limit}',
                           f"{verb} {ot.plural.lower()} the departed identity owns")
    else:   # the list can't filter on owner: take the first PAGE and pick theirs with JSONPath
        params, display = f"limit:{PAGE}", f"{verb} {ot.plural.lower()} (all; theirs are picked out next)"
    return _http(t, name, display, "get", f"{t.api_url}{ot.path}", next_step, url_params=params, headers=ot.headers)


def _reassign_loop(t: Target, ot: ObjectType, next_step: str) -> dict[str, Any]:
    patch_step, done_step = f"Patch {ot.singular} Owner", f"{ot.singular} Reassigned"
    # Steps inside a loop only see $.loop.*: the run's values come in through context ("$").
    patch = [{"op": "replace", "path": "/owner",
              "value": {"type": "IDENTITY", "id": "{{$.loop.context." + NEW_OWNER_ID[2:] + "}}"}}]
    return {
        "actionId": "sp:loop:iterator", "type": "action", "versionNumber": 1,
        "displayName": f"Give each {ot.singular.lower()} to the new owner",
        "attributes": {
            "input.$": ot.owned("Get Owned"),
            "context.$": "$",
            "start": patch_step,
            "steps": {
                patch_step: _http(t, patch_step, f"Set the {ot.singular.lower()}'s owner", "patch",
                                  f"{t.api_url}{ot.item_path}/{{{{$.loop.loopInput.id}}}}", done_step,
                                  headers=ot.headers, patch=patch),
                done_step: _success(),
            },
        },
        "nextStep": next_step,
    }


# ── Pending approvals (cfg.pending_approvals) ──────────────────────────────────
# The departed identity's pending access-request and generic (form/workflow) approvals, from the unified approvals
# list (verified live: an admin may pass another identity's approverId). Each is reassigned to the new owner; SailPoint
# records it in reassignmentHistory (MANUAL_REASSIGNMENT, by the workflow's PAT user) with the comment.
APPROVALS_LIST_PARAMS = 'approverId:{{' + DEPARTED_ID + '}}\nfilters:status eq "PENDING"\nlimit:'
# approverId also matches approvals assigned to a governance group the identity is in (verified live): those stay with
# the group, so only approvals assigned to the identity itself are picked out (an approval with several assignees
# moves only when the departed identity is the first).
ASSIGNED_TO_DEPARTED = "[?(@.assignedTo[0].identityID == " + DEPARTED_ID + ")]"
REASSIGN_COMMENT = "Reassigned automatically: the approver has left (workflow {workflow})."


def _list_approvals(t: Target, name: str, verb: str, limit: int, next_step: str) -> dict[str, Any]:
    return _http(t, name, f"{verb} the departed identity's pending approvals", "get",
                 f"{t.api_url}{api.GENERIC_APPROVALS}", next_step, url_params=f"{APPROVALS_LIST_PARAMS}{limit}")


def _reassign_approvals_loop(cfg: Config, t: Target, next_step: str) -> dict[str, Any]:
    # The comment is fixed text (the workflow name comes from the config prefix): a templated value is spliced into
    # the JSON raw, so a name with a quote in it would break the body.
    body = {"reassignTo": "{{$.loop.context." + NEW_OWNER_ID[2:] + "}}",
            "comment": REASSIGN_COMMENT.format(workflow=cfg.workflow_name)}
    return {
        "actionId": "sp:loop:iterator", "type": "action", "versionNumber": 1,
        "displayName": "Give each pending approval to the new owner",
        "attributes": {
            "input.$": "$." + _camel(GET_APPROVALS) + ".body" + ASSIGNED_TO_DEPARTED,
            "context.$": "$",
            "start": REASSIGN_APPROVAL,
            "steps": {
                REASSIGN_APPROVAL: _http(t, REASSIGN_APPROVAL, "Reassign the approval", "post",
                                         f"{t.api_url}{api.GENERIC_APPROVALS}/{{{{$.loop.loopInput.id}}}}/reassign",
                                         APPROVAL_REASSIGNED, body=body),
                APPROVAL_REASSIGNED: _success(),
            },
        },
        "nextStep": next_step,
    }


# ── Open certifications (cfg.pending_certifications) ──────────────────────────
# The departed identity's active, unfinished certifications as reviewer (reviewer-identity takes someone else's ID for
# an admin; verified live). Each moves as a whole to the new owner through its campaign's reassign call, the only one
# that moves a whole certification in one call (the certification's own reassign moves review items, which would need
# a loop inside the loop).
CERTS_LIST_PARAMS = ('reviewer-identity:{{' + DEPARTED_ID + '}}\nfilters:phase eq "ACTIVE" and completed eq false'
                     '\nlimit:')
REVIEWED_BY_DEPARTED = "[?(@.reviewer.id == " + DEPARTED_ID + ")]"
CERT_REASON = "Reassigned automatically: the reviewer has left (workflow {workflow})."


def _list_certs(t: Target, name: str, verb: str, limit: int, next_step: str) -> dict[str, Any]:
    return _http(t, name, f"{verb} the departed identity's open certifications", "get",
                 f"{t.api_url}{api.CERTIFICATIONS}", next_step, url_params=f"{CERTS_LIST_PARAMS}{limit}")


def _wait(display: str, duration: str, next_step: str) -> dict[str, Any]:
    return {"actionId": "sp:sleep", "type": "action", "versionNumber": 1, "displayName": display,
            "attributes": {"type": "waitFor", "duration": duration}, "nextStep": next_step}


def _reassign_certs_loop(cfg: Config, t: Target, next_step: str) -> dict[str, Any]:
    body = {"certificationIds": ["{{$.loop.loopInput.id}}"],
            "reassignTo": {"id": "{{$.loop.context." + NEW_OWNER_ID[2:] + "}}", "type": "IDENTITY"},
            "reason": CERT_REASON.format(workflow=cfg.workflow_name)}
    return {
        "actionId": "sp:loop:iterator", "type": "action", "versionNumber": 1,
        "displayName": "Give each open certification to the new owner",
        "attributes": {
            "input.$": "$." + _camel(GET_CERTS) + ".body" + REVIEWED_BY_DEPARTED,
            "context.$": "$",
            "start": REASSIGN_CERT,
            "steps": {
                REASSIGN_CERT: _http(t, REASSIGN_CERT, "Reassign the certification", "post",
                                     f"{t.api_url}{api.CAMPAIGNS}/{{{{$.loop.loopInput.campaign.id}}}}/reassign",
                                     CERT_REASSIGNED, body=body),
                CERT_REASSIGNED: _success(),
            },
        },
        "nextStep": next_step,
    }


@dataclass(frozen=True)
class PendingWork:
    """One kind of pending work the workflow moves after the objects: find, choose, loop, recheck."""
    find: str
    has: str
    loop: str
    recheck: str
    loop_step: str
    noun: str                 # "pending approvals"
    filter: str               # JSONPath filter keeping what is the departed identity's own

    def first(self, step: str) -> str:
        return "$." + _camel(step) + ".body" + self.filter + ".id"


APPROVALS = PendingWork(GET_APPROVALS, HAS_APPROVALS, REASSIGN_APPROVALS, RECHECK_APPROVALS, REASSIGN_APPROVAL,
                        "pending approvals", ASSIGNED_TO_DEPARTED)
CERTIFICATIONS = PendingWork(GET_CERTS, HAS_CERTS, REASSIGN_CERTS, RECHECK_CERTS, REASSIGN_CERT,
                             "open certifications", REVIEWED_BY_DEPARTED)


def pending_work(cfg: Config) -> list[PendingWork]:
    return [k for k, on in ((APPROVALS, cfg.pending_approvals), (CERTIFICATIONS, cfg.pending_certifications)) if on]


ALERT_BODY = (
    "<p>The workflow <b>$!{workflow}</b> could not move everything that <b>$!{departed}</b> owned.</p>"
    "<p>Some of the objects they owned (or approvals waiting for them) are still theirs (new owner: $!{newOwner}, "
    "chosen as: $!{ownerSource}).</p>"
    "<p>Open <b>Admin → Workflows → $!{workflow} → Execution history</b> for the details, fix the cause, "
    "and reassign what is left.</p>")


def _alert(cfg: Config, next_step: str) -> dict[str, Any]:
    # The body is fixed Velocity markup; every value comes in through context as JSONPath (never templated in).
    return {"actionId": "sp:send-email", "type": "action", "versionNumber": 2,
            "displayName": "Tell the admins what's left",
            "attributes": {
                "recipientEmailList": list(cfg.alert_emails),
                "subject": "Ownership not fully reassigned after a termination",
                "body": ALERT_BODY,
                "context": {"workflow": cfg.workflow_name, "departed.$": "$.trigger.identity.name",
                            "newOwner.$": NEW_OWNER_ID, "ownerSource.$": f"{OWNER_VARS}.ownerSource"}},
            "nextStep": next_step}


def description(cfg: Config) -> str:
    names = [OBJECT_TYPES[k].plural.lower() for k in cfg.object_types]
    what = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    to = ("their manager, or the fallback owner when they have no active manager"
          if cfg.owner_strategy == "manager-then-fallback" else "the fallback owner")
    return (f"When an identity's lifecycle state becomes {' or '.join(cfg.termination_states)}, gives the {what} "
            f"they own" + "".join(f" and their {w.noun}" for w in pending_work(cfg)) + f" to {to}.")


def step_descriptions(cfg: Config) -> dict[str, str]:
    """What each step checks or does, and why: shown as the step's Description in the workflow builder."""
    inactive = " or ".join(cfg.inactive_states) or "an inactive state"
    d = {
        GET_DEPARTED: "Looks up the person who left, to find their manager. Nothing is changed here.",
        DEFINE_OWNER: "Starts with the fallback owner as the new owner (reason: fallback). The manager replaces them "
                      "below if usable. To change the fallback owner, edit newOwnerId here.",
        HAS_MANAGER: "Do they have a manager? Without one, everything goes to the fallback owner.",
        GET_MANAGER: "Looks up the manager, to check that they're still active.",
        MANAGER_INACTIVE: f"Has the manager left too (lifecycle state {inactive})? Then the fallback owner is used "
                          "instead, so nothing ends up with someone who can't act on it.",
        USE_MANAGER: "The manager is usable: makes them the new owner (reason: manager).",
        OWNER_USABLE: "Stops if the new owner would be the person who left (for example, they are the fallback owner) "
                      "or the fallback owner was never filled in. Reassigning would then change nothing or fail.",
        GET_NEW_OWNER: "Looks up the new owner before anything changes. A governance group given an unknown owner loses "
                       "its owner without an error, so the owner must exist first. An unknown ID stops the run here.",
        NEW_OWNER_FOUND: "Continues only if the new owner exists.",
        NEW_OWNER_ACTIVE: f"Stops if the new owner has left too (lifecycle state {inactive}): they couldn't act as owner.",
        GET_APPROVALS: "Lists the approvals waiting for the person who left: access requests, forms and workflows (up "
                       f"to {PAGE}). It also returns approvals of governance groups they're in; those are left out next.",
        HAS_APPROVALS: "Are any pending approvals assigned to them personally? Approvals assigned to a governance group "
                       "stay with the group, so they're not counted.",
        REASSIGN_APPROVALS: "Gives each of their own pending approvals to the new owner, with a comment. SailPoint records "
                            "the reassignment in the approval's history.",
        REASSIGN_APPROVAL: "Reassigns this approval to the new owner.",
        APPROVAL_REASSIGNED: "This approval is done.",
        RECHECK_APPROVALS: "Checks that no pending approval is still assigned to them personally (a reassignment can fail, "
                           f"or they had more than {PAGE}).",
        GET_CERTS: f"Lists the active, unfinished certifications they're the reviewer of (up to {PAGE}).",
        HAS_CERTS: "Do they review any open certification themselves? If not, the next step is skipped.",
        REASSIGN_CERTS: "Moves each whole certification to the new owner through its campaign, with a reason, so the "
                        "review isn't left with someone who has left.",
        REASSIGN_CERT: "Queues the move of this certification to the new owner.",
        CERT_REASSIGNED: "This certification is queued.",
        WAIT_CERTS: "Waits a minute. SailPoint queues certification moves, and they still show under the person who "
                    "left for a few seconds; checking straight away could report a false failure.",
        RECHECK_CERTS: "Checks that they no longer review any open certification (a move can fail, or they had more "
                       f"than {PAGE}).",
        ANYTHING_LEFT: "Is anything still theirs? If so, the run ends as Failed (after the alert email, if set). "
                       "Otherwise it ends as Done.",
        ALERT_ADMINS: "Emails the admins that some objects or pending work could not be moved, so someone follows up.",
        DONE: "Everything they owned, and everything waiting for them, has a new owner.",
    }
    for ot in OBJECT_TYPES.values():
        what, one = ot.plural.lower(), ot.singular.lower()
        d[ot.step("Get Owned")] = (f"Lists the {what} the person who left owns (up to {PAGE})." if ot.server_filter else
                                   f"Lists the first {PAGE} {what} in the tenant. This list can't filter by owner, so "
                                   "theirs are picked out in the next step.")
        d[ot.step("Owns")] = f"Do they own any {what}? If not, the next step is skipped."
        d[ot.step("Reassign")] = (f"Gives each of their {what} to the new owner, in parallel (up to {PAGE}). A failed "
                                  "change affects only that one; the recheck at the end reports it.")
        d[f"Patch {ot.singular} Owner"] = f"Sets this {one}'s owner to the new owner."
        d[f"{ot.singular} Reassigned"] = f"This {one} is done."
        d[ot.step("Recheck")] = (f"Checks that they no longer own any {what} (a change can fail, or they owned more "
                                 f"than {PAGE})." if ot.server_filter else
                                 f"Checks again that none of the first {PAGE} {what} is still theirs.")
    return d


def _describe(steps: dict[str, Any], descriptions: Mapping[str, str]) -> None:
    """Gives every step (loop bodies too) its description, keeping a failure step's own."""
    for name, step in steps.items():
        if step.get("type") != "failure" and name in descriptions:
            step["description"] = descriptions[name]
        if step.get("actionId") == "sp:loop:iterator":
            _describe(step["attributes"]["steps"], descriptions)


def build(cfg: Config, t: Target = Target()) -> dict[str, Any]:
    """The workflow body for POST/PUT /workflows/v1 (created disabled; `apply --enable` turns it on)."""
    types = [OBJECT_TYPES[k] for k in cfg.object_types]
    steps: dict[str, Any] = {}

    steps[GET_DEPARTED] = {"actionId": "sp:get-identity", "type": "action", "versionNumber": 2,
                           "displayName": "The identity who left", "attributes": {"id.$": DEPARTED_ID},
                           "nextStep": DEFINE_OWNER}
    after_define = HAS_MANAGER if cfg.owner_strategy == "manager-then-fallback" else OWNER_USABLE
    steps[DEFINE_OWNER] = {
        "attributes": {"id": "sp:define-variable", "variables": [
            {"name": "newOwnerId", "description": "Who gets the objects: the fallback owner unless a usable manager "
                                                  "is found. Edit it here to change the fallback (an identity ID).",
             "variableA": t.fallback_owner_id, "transforms": []},
            {"name": "ownerSource", "description": "Why that owner: fallback or manager",
             "variableA": "fallback", "transforms": []}]},
        "type": "Mutation", "displayName": "New owner: the fallback, until a manager is found",
        "nextStep": after_define}

    if cfg.owner_strategy == "manager-then-fallback":
        steps[HAS_MANAGER] = _choice("Has a manager?", [_present("$.getDepartedIdentity.managerRef.id", GET_MANAGER)],
                                     OWNER_USABLE)
        steps[GET_MANAGER] = {"actionId": "sp:get-identity", "type": "action", "versionNumber": 2,
                              "displayName": "The manager", "attributes": {"id.$": "$.getDepartedIdentity.managerRef.id"},
                              "nextStep": MANAGER_INACTIVE if cfg.inactive_states else USE_MANAGER}
        if cfg.inactive_states:
            steps[MANAGER_INACTIVE] = _choice(
                "Manager left too?",
                [{"comparator": "StringMatches", "variableA.$": "$.getManager.attributes.cloudLifecycleState",
                  "variableB": state_regex(cfg.inactive_states), "nextStep": OWNER_USABLE}],
                USE_MANAGER, action="sp:compare-strings")
        steps[USE_MANAGER] = {
            "attributes": {"id": "sp:update-variable", "variables": [
                {"name": NEW_OWNER_ID, "description": "", "variableA.$": "$.getManager.id", "transforms": []},
                {"name": f"{OWNER_VARS}.ownerSource", "description": "", "variableA": "manager", "transforms": []}]},
            "type": "Mutation", "displayName": "New owner: the manager", "nextStep": OWNER_USABLE}

    first = types[0].step("Get Owned")
    steps[OWNER_USABLE] = _choice(
        "New owner usable?",
        [{"comparator": "StringEquals", "variableA.$": NEW_OWNER_ID, "variableB.$": DEPARTED_ID,
          "nextStep": STOP_NO_OWNER},
         {"comparator": "StringMatches", "variableA.$": NEW_OWNER_ID, "variableB": "^__", "nextStep": STOP_NO_OWNER}],
        GET_NEW_OWNER, action="sp:compare-strings")
    # The new owner must exist (and be active) before anything changes: a governance group PATCHed to an unknown
    # identity ID clears its owner instead of failing (verified live), which no later check would notice.
    steps[GET_NEW_OWNER] = {"actionId": "sp:get-identity", "type": "action", "versionNumber": 2,
                            "displayName": "The new owner", "attributes": {"id.$": NEW_OWNER_ID},
                            "nextStep": NEW_OWNER_FOUND}
    after_found = NEW_OWNER_ACTIVE if cfg.inactive_states else first
    steps[NEW_OWNER_FOUND] = _choice("New owner exists?", [_present("$.getNewOwner.id", after_found)], STOP_NO_OWNER)
    if cfg.inactive_states:
        steps[NEW_OWNER_ACTIVE] = _choice(
            "New owner left too?",
            [{"comparator": "StringMatches", "variableA.$": "$.getNewOwner.attributes.cloudLifecycleState",
              "variableB": state_regex(cfg.inactive_states), "nextStep": STOP_NO_OWNER}],
            first, action="sp:compare-strings")
    steps[STOP_NO_OWNER] = _failure(
        "No usable new owner",
        "Nothing was changed: the new owner would be the departed identity itself, the fallback owner ID was never "
        "filled in, or the fallback owner no longer exists or has left too. Set a fallback owner who is not leaving "
        "(Define Variable Owner → newOwnerId).")

    first_recheck = types[0].step("Recheck")
    work = pending_work(cfg)
    after_objects = work[0].find if work else first_recheck
    for i, ot in enumerate(types):
        after = types[i + 1].step("Get Owned") if i + 1 < len(types) else after_objects
        steps[ot.step("Get Owned")] = _list_owned(t, ot.step("Get Owned"), ot, "Find", PAGE, ot.step("Owns"))
        steps[ot.step("Owns")] = _choice(f"Owns any {ot.plural.lower()}?",
                                         [_present(ot.first_owned_id("Get Owned"), ot.step("Reassign"))], after)
        steps[ot.step("Reassign")] = _reassign_loop(t, ot, after)
    lister = {APPROVALS: _list_approvals, CERTIFICATIONS: _list_certs}
    looper = {APPROVALS: _reassign_approvals_loop, CERTIFICATIONS: _reassign_certs_loop}
    for i, w in enumerate(work):
        after = work[i + 1].find if i + 1 < len(work) else first_recheck
        steps[w.find] = lister[w](t, w.find, "Find", PAGE, w.has)
        steps[w.has] = _choice(f"Any {w.noun}?", [_present(w.first(w.find), w.loop)], after)
        if w is CERTIFICATIONS:    # the reassign is queued: let it land before anything rechecks
            steps[w.loop] = looper[w](cfg, t, WAIT_CERTS)
            steps[WAIT_CERTS] = _wait("Let the certification moves finish", CERT_WAIT, after)
        else:
            steps[w.loop] = looper[w](cfg, t, after)

    # Check that nothing is left: catches failed loop iterations and owners of more than PAGE objects.
    last_recheck = work[0].recheck if work else ANYTHING_LEFT
    for i, ot in enumerate(types):
        after = types[i + 1].step("Recheck") if i + 1 < len(types) else last_recheck
        steps[ot.step("Recheck")] = _list_owned(t, ot.step("Recheck"), ot, "Recheck", 1 if ot.server_filter else PAGE,
                                                after)
    leftovers = ALERT_ADMINS if cfg.alert_emails else STOP_LEFTOVERS
    left = [_present(ot.first_owned_id("Recheck"), leftovers) for ot in types]
    for i, w in enumerate(work):
        # A full page, filtered like the first list: someone else's rows (a group's) would hide a leftover at limit 1.
        steps[w.recheck] = lister[w](t, w.recheck, "Recheck", PAGE, work[i + 1].recheck if i + 1 < len(work) else ANYTHING_LEFT)
        left.append(_present(w.first(w.recheck), leftovers))
    steps[ANYTHING_LEFT] = _choice("Anything still owned?", left, DONE)
    if cfg.alert_emails:
        steps[ALERT_ADMINS] = _alert(cfg, STOP_LEFTOVERS)
    steps[STOP_LEFTOVERS] = _failure(
        "Ownership not fully reassigned",
        f"The departed identity still owns some {', '.join(ot.plural.lower() for ot in types)}"
        + "".join(f" or has {w.noun}" for w in pending_work(cfg)) + " after the "
        f"reassignment: a change failed, or they had more than {PAGE} of one kind (for governance groups and "
        f"identity profiles: theirs weren't among the first {PAGE}). Check this run's history, "
        "then run the workflow again or reassign the rest by hand.")
    steps[DONE] = _success()
    _describe(steps, step_descriptions(cfg))

    return {
        "name": cfg.workflow_name,
        "description": description(cfg),
        "owner": _owner(t.workflow_owner_id, t.workflow_owner_name),
        "enabled": False,
        "definition": {"start": GET_DEPARTED, "steps": steps},
        "trigger": {"type": "EVENT", "attributes": {"id": TRIGGER_ID,
                                                    "filter.$": trigger_filter(cfg.termination_states)}},
    }


def http_step_names(cfg: Config) -> list[str]:
    """Names of every HTTP Request step `build` makes for `cfg` (top level and inside loops)."""
    names = [n for k in cfg.object_types for n in (OBJECT_TYPES[k].step("Get Owned"),
                                                    f"Patch {OBJECT_TYPES[k].singular} Owner",
                                                    OBJECT_TYPES[k].step("Recheck"))]
    for w in pending_work(cfg):
        names += [w.find, w.loop_step, w.recheck]
    return names


def ref_ids_of(workflow: Mapping[str, Any]) -> dict[str, str]:
    """The refIDs an installed workflow's HTTP steps already use, by step name (loops included), so an update
    keeps the same parameter references instead of registering new ones."""
    found: dict[str, str] = {}

    def walk(steps: Mapping[str, Any]) -> None:
        for name, step in (steps or {}).items():
            attrs = step.get("attributes") or {}
            ref = (attrs.get("param_oauth") or {}).get("refID")
            if step.get("actionId") == "sp:http" and ref:
                found[name] = ref
            if step.get("actionId") == "sp:loop:iterator":
                walk(attrs.get("steps") or {})

    walk(((workflow.get("definition") or {}).get("steps")) or {})
    return found


def test_input(identity_id: str, identity_name: str, new_state: str, old_state: str = "active") -> dict[str, Any]:
    """A trigger payload for POST /workflows/v1/{id}/test (the trigger's documented input shape)."""
    return {"identity": {"id": identity_id, "name": identity_name, "type": "IDENTITY"},
            "identityProfile": None, "newLifecycleState": new_state, "oldLifecycleState": old_state}


def pretty(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"
