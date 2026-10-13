"""The one config loader. Every organisation-specific setting lives in `config/<tenant>.json`
(schema: `config/ownership.example.json`); nothing tenant-specific is ever written into code.

Identities in the config (`fallbackOwner`, `workflowOwner`) may be given as an ID, a username or an
email address; the installer resolves them in the tenant (`reassign/ops.py`), so the same file shape
works in any tenant.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

OBJECT_TYPE_KEYS = ("roles", "accessProfiles", "entitlements", "sources", "apps", "governanceGroups", "identityProfiles")
OWNER_STRATEGIES = ("manager-then-fallback", "fallback-only")
DEFAULT_TERMINATION_STATES = ("terminated",)
DEFAULT_INACTIVE_STATES = ("terminated", "archived")
DEFAULT_PARAMETER = "Ownership Reassignment API"

# Lifecycle state technical names: letters, digits, '_' and '-' (ISC's own are e.g. "preHire", "leaveOfAbsence").
STATE_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
PREFIX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{0,31}$")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

WORKFLOW_OWNER_NOTE = ("workflowOwner is not set, so the PAT user owns the workflow. For production, use a "
                       "dedicated admin service identity: a workflow whose owner leaves can stop running.")


class ConfigError(ValueError):
    """The config file is missing, unreadable or invalid; the message says what to fix."""


@dataclass(frozen=True)
class Config:
    path: str
    prefix: str
    env_file: str | None
    fallback_owner: str | None
    workflow_owner: str | None
    owner_strategy: str
    termination_states: tuple[str, ...]
    inactive_states: tuple[str, ...]
    object_types: tuple[str, ...]
    alert_emails: tuple[str, ...]
    parameter: str
    pending_approvals: bool = True

    @property
    def workflow_name(self) -> str:
        return f"{self.prefix} Reassign Ownership on Termination" if self.prefix else "Reassign Ownership on Termination"

    @property
    def test_object_prefix(self) -> str:
        """Names of the throwaway objects `ownership.py test` creates (and is allowed to delete)."""
        return f"{self.prefix or 'ZZ'} Ownership Test"


def defaults(path: str = "<defaults>") -> Config:
    """The settings used when a key is missing; also what `export` builds the committed JSON from."""
    return Config(path=path, prefix="", env_file=".env", fallback_owner=None, workflow_owner=None,
                  owner_strategy=OWNER_STRATEGIES[0], termination_states=DEFAULT_TERMINATION_STATES,
                  inactive_states=DEFAULT_INACTIVE_STATES, object_types=OBJECT_TYPE_KEYS, alert_emails=(),
                  parameter=DEFAULT_PARAMETER, pending_approvals=True)


def _states(raw: Any, key: str, default: tuple[str, ...], *, allow_empty: bool = False) -> tuple[str, ...]:
    if raw is None:
        return default
    if not isinstance(raw, list) or not all(isinstance(s, str) for s in raw):
        raise ConfigError(f"{key} must be a list of lifecycle state technical names, e.g. [\"terminated\"]")
    if not raw and not allow_empty:
        raise ConfigError(f"{key} needs at least one lifecycle state")
    bad = [s for s in raw if not STATE_NAME.match(s)]
    if bad:
        raise ConfigError(f"{key}: {bad} are not lifecycle state technical names (letters, digits, '_' or '-'); "
                          "use the technical name from the identity profile, not the display name")
    return tuple(dict.fromkeys(raw))


def _identity_ref(raw: Any, key: str) -> str | None:
    if raw is None or raw == "":
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise ConfigError(f"{key} must be an identity ID, username or email address")
    return raw.strip()


def from_dict(data: dict[str, Any], path: str = "<dict>") -> Config:
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: the config must be a JSON object")
    d = defaults(path)

    prefix = data.get("prefix", "")
    if prefix and (not isinstance(prefix, str) or not PREFIX.match(prefix)):
        raise ConfigError("prefix must be 1-32 letters, digits, spaces, '_' or '-', starting with a letter or digit")

    strategy = data.get("ownerStrategy", d.owner_strategy)
    if strategy not in OWNER_STRATEGIES:
        raise ConfigError(f"ownerStrategy must be one of {list(OWNER_STRATEGIES)}")

    types_raw = data.get("objectTypes")
    if types_raw is None:
        types = d.object_types
    else:
        if not isinstance(types_raw, dict) or not all(isinstance(v, bool) for v in types_raw.values()):
            raise ConfigError(f"objectTypes must map {list(OBJECT_TYPE_KEYS)} to true or false")
        unknown = sorted(set(types_raw) - set(OBJECT_TYPE_KEYS))
        if unknown:
            raise ConfigError(f"objectTypes: unknown {unknown}; supported: {list(OBJECT_TYPE_KEYS)}")
        types = tuple(k for k in OBJECT_TYPE_KEYS if types_raw.get(k, True))
        if not types:
            raise ConfigError("objectTypes turns every object type off; enable at least one")

    emails = data.get("alertEmails") or []
    if not isinstance(emails, list) or not all(isinstance(e, str) and EMAIL.match(e) for e in emails):
        raise ConfigError("alertEmails must be a list of email addresses (or empty)")
    if len(emails) > 10:
        raise ConfigError("alertEmails takes at most 10 addresses (the Send Email action's limit); use a mailing list")

    http = data.get("http") or {}
    if not isinstance(http, dict):
        raise ConfigError("http must be an object: {\"parameter\": \"<Parameter Storage name or ID>\"}")
    unknown_http = sorted(set(http) - {"parameter"})
    if unknown_http:
        raise ConfigError(f"http: unknown {unknown_http}. The HTTP steps' PAT lives in Parameter Storage; "
                          "name it with http.parameter (README: Prerequisites)")
    parameter = http.get("parameter", d.parameter)
    if not isinstance(parameter, str) or not parameter.strip():
        raise ConfigError("http.parameter must be the name or ID of an OAuth 2.0 Client Credentials parameter "
                          "in Parameter Storage")

    pending = data.get("pendingApprovals", d.pending_approvals)
    if not isinstance(pending, bool):
        raise ConfigError("pendingApprovals must be true or false")

    env_file = data.get("envFile", d.env_file)
    if env_file is not None and not isinstance(env_file, str):
        raise ConfigError("envFile must be a path (relative to the current directory) or null")

    return Config(
        path=path,
        prefix=prefix or "",
        env_file=env_file or None,
        fallback_owner=_identity_ref(data.get("fallbackOwner"), "fallbackOwner"),
        workflow_owner=_identity_ref(data.get("workflowOwner"), "workflowOwner"),
        owner_strategy=strategy,
        termination_states=_states(data.get("terminationStates"), "terminationStates", d.termination_states),
        inactive_states=_states(data.get("inactiveStates"), "inactiveStates", d.inactive_states, allow_empty=True),
        object_types=types,
        alert_emails=tuple(emails),
        parameter=parameter.strip(),
        pending_approvals=pending,
    )


def load(path: str | os.PathLike[str]) -> Config:
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"{p}: not found. Copy config/ownership.example.json to config/<tenant>.json and edit it.")
    try:
        data = json.loads(p.read_text())
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{p}: not valid JSON ({exc})") from exc
    return from_dict(data, str(p))
