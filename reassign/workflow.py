"""Pure builder for the "Reassign Ownership on Termination" workflow (no I/O; standard library only).

Flow (see README.md for the picture):
  trigger (lifecycle state changed → a termination state)
  → Get Departed Identity → Define Variable Owner (the fallback)
  → [manager-then-fallback] Has Manager? → Get Manager → Manager Inactive? → Use Manager
  → Owner Usable?  (not the departed identity itself, not an unfilled placeholder)
  → for each enabled object type: Get Owned X → Owns X? → Reassign X (loop: Patch X Owner)
  → Recheck X … → Anything Left? → [Alert Admins] → failure | success

Facts this relies on are listed, with how they were verified, in CLAUDE.md and docs/RESEARCH.md.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping

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
STOP_NO_OWNER = "Stop - No Usable Owner"
ANYTHING_LEFT = "Anything Left?"
ALERT_ADMINS = "Alert Admins"
STOP_LEFTOVERS = "Stop - Ownership Not Fully Reassigned"
DONE = "Done"

DEPARTED_ID = "$.trigger.identity.id"
NEW_OWNER_ID = f"{OWNER_VARS}.newOwnerId"

# A list call returns at most this many objects; the loop step's hard limit is 250 iterations.
PAGE = 250


@dataclass(frozen=True)
class ObjectType:
    key: str        # config objectTypes key
    plural: str     # "Roles", used in step names
    singular: str   # "Role"
    path: str       # list endpoint; PATCH is <path>/<id>

    def step(self, verb: str) -> str:
        return f"{verb} {self.plural}"

    def output(self, verb: str) -> str:
        return "$." + _camel(self.step(verb))


OBJECT_TYPES: dict[str, ObjectType] = {
    "roles": ObjectType("roles", "Roles", "Role", "/v3/roles"),
    "accessProfiles": ObjectType("accessProfiles", "Access Profiles", "Access Profile", "/v3/access-profiles"),
    "sources": ObjectType("sources", "Sources", "Source", "/v3/sources"),
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
          url_params: str | None = None, patch: list[dict[str, Any]] | None = None) -> dict[str, Any]:
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
    if patch is not None:
        attrs["requestContentType"] = "json-patch+json"
        attrs["jsonPatchRequestBody"] = patch
    else:
        attrs["requestContentType"] = "json"
    step = {"actionId": "sp:http", "type": "action", "versionNumber": 3, "displayName": display, "attributes": attrs}
    if next_step:
        step["nextStep"] = next_step
    return step


def _list_owned(t: Target, name: str, ot: ObjectType, verb: str, limit: int, next_step: str) -> dict[str, Any]:
    params = f'filters:owner.id eq "{{{{{DEPARTED_ID}}}}}"\nlimit:{limit}'
    return _http(t, name, f"{verb} {ot.plural.lower()} the departed identity owns", "get", f"{t.api_url}{ot.path}",
                 next_step, url_params=params)


def _reassign_loop(t: Target, ot: ObjectType, next_step: str) -> dict[str, Any]:
    patch_step, done_step = f"Patch {ot.singular} Owner", f"{ot.singular} Reassigned"
    # Steps inside a loop only see $.loop.*: the run's values come in through context ("$").
    patch = [{"op": "replace", "path": "/owner",
              "value": {"type": "IDENTITY", "id": "{{$.loop.context." + NEW_OWNER_ID[2:] + "}}"}}]
    return {
        "actionId": "sp:loop:iterator", "type": "action", "versionNumber": 1,
        "displayName": f"Give each {ot.singular.lower()} to the new owner",
        "attributes": {
            "input.$": ot.output("Get Owned") + ".body",
            "context.$": "$",
            "start": patch_step,
            "steps": {
                patch_step: _http(t, patch_step, f"Set the {ot.singular.lower()}'s owner", "patch",
                                  f"{t.api_url}{ot.path}/{{{{$.loop.loopInput.id}}}}", done_step, patch=patch),
                done_step: _success(),
            },
        },
        "nextStep": next_step,
    }


ALERT_BODY = (
    "<p>The workflow <b>$!{workflow}</b> could not move everything that <b>$!{departed}</b> owned.</p>"
    "<p>Some roles, access profiles or sources are still owned by this identity (new owner: $!{newOwner}, "
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
            f"they own to {to}.")


def build(cfg: Config, t: Target = Target()) -> dict[str, Any]:
    """The workflow body for POST/PUT /v2025/workflows (created disabled; `apply --enable` turns it on)."""
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
        first, action="sp:compare-strings")
    steps[STOP_NO_OWNER] = _failure(
        "No usable new owner",
        "Nothing was changed: the new owner would be the departed identity itself, or the fallback owner ID was "
        "never filled in. Set a fallback owner who is not leaving (Define Variable Owner → newOwnerId).")

    for i, ot in enumerate(types):
        after = types[i + 1].step("Get Owned") if i + 1 < len(types) else types[0].step("Recheck")
        steps[ot.step("Get Owned")] = _list_owned(t, ot.step("Get Owned"), ot, "Find", PAGE, ot.step("Owns"))
        steps[ot.step("Owns")] = _choice(f"Owns any {ot.plural.lower()}?",
                                         [_present(ot.output("Get Owned") + ".body[0].id", ot.step("Reassign"))], after)
        steps[ot.step("Reassign")] = _reassign_loop(t, ot, after)

    # Check that nothing is left: catches failed loop iterations and owners of more than PAGE objects.
    for i, ot in enumerate(types):
        after = types[i + 1].step("Recheck") if i + 1 < len(types) else ANYTHING_LEFT
        steps[ot.step("Recheck")] = _list_owned(t, ot.step("Recheck"), ot, "Recheck", 1, after)
    leftovers = ALERT_ADMINS if cfg.alert_emails else STOP_LEFTOVERS
    steps[ANYTHING_LEFT] = _choice("Anything still owned?",
                                   [_present(ot.output("Recheck") + ".body[0].id", leftovers) for ot in types], DONE)
    if cfg.alert_emails:
        steps[ALERT_ADMINS] = _alert(cfg, STOP_LEFTOVERS)
    steps[STOP_LEFTOVERS] = _failure(
        "Ownership not fully reassigned",
        f"The departed identity still owns some {', '.join(ot.plural.lower() for ot in types)} after the "
        f"reassignment: a change failed, or they owned more than {PAGE} of one type. Check this run's history, "
        "then run the workflow again or reassign the rest by hand.")
    steps[DONE] = _success()

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
    return [n for k in cfg.object_types for n in (OBJECT_TYPES[k].step("Get Owned"),
                                                   f"Patch {OBJECT_TYPES[k].singular} Owner",
                                                   OBJECT_TYPES[k].step("Recheck"))]


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
    """A trigger payload for POST /v2025/workflows/{id}/test (the trigger's documented input shape)."""
    return {"identity": {"id": identity_id, "name": identity_name, "type": "IDENTITY"},
            "identityProfile": None, "newLifecycleState": new_state, "oldLifecycleState": old_state}


def pretty(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"
