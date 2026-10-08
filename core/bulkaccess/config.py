"""Load and validate the one tenant config file (see config/bulk-access.example.json).

Every setting for both deployments lives in that one JSON file, organised by
concern rather than by deployment, so the Launcher (native form) and the UI
plugin always behave the same way. Where SailPoint forces a route-specific value
(the Launcher form's 30-person picker, no end-date input on the Launcher), this
module derives it, so no installer re-implements a rule.

Credentials are never read from the config itself: SAIL_BASE_URL / SAIL_CLIENT_ID /
SAIL_CLIENT_SECRET come from the environment or from the .env file the config names.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MODES = ("dry-run", "live")
ITEM_TYPES = ("ACCESS_PROFILE", "ROLE", "ENTITLEMENT")
TIMEOUT_ACTIONS = ("EXPIRED", "APPROVED")
PRIORITIES = ("LOW", "MEDIUM", "HIGH")
DEPLOYMENTS = ("launcher", "plugin")

# A form SELECT accepts at most 30 selections (hard UI limit in SailPoint forms).
FORM_SELECT_MAX = 30
# The workflow Loop operator (sp:loop:iterator) rejects inputs over 250 items
# ("Input has N iterations which exceed 250 iteration limit", verified live), so one
# workflow run -- one approval -- covers at most 250 people. Bigger lists are split
# into parts. (The Serial Loop operator is no use here: it silently stops after 50.)
LOOP_MAX = 250

TEMPORARY_MODES = ("duration", "endDate")
# Manage Access (v2) `removeDuration` strings are "<n><suffix>" (verified live:
# "2h", "1d", "1w", "1M"; "" or a missing value means permanent access).
DURATION_UNITS = {"HOURS": "h", "DAYS": "d", "WEEKS": "w", "MONTHS": "M"}
# Upper bound in days of one unit, for checking `temporaryAccess.maxDays`.
UNIT_MAX_DAYS = {"HOURS": 1 / 24, "DAYS": 1, "WEEKS": 7, "MONTHS": 31}


class ConfigError(ValueError):
    """The config file is missing or invalid; the message says what to fix."""


@dataclass(frozen=True)
class Config:
    prefix: str
    mode: str
    inc_pattern: str
    inc_example: str
    inc_message: str
    catalog_types: tuple[str, ...]
    catalog_name_starts_with: str | None
    catalog_max_items: int
    people_max: int | None
    approval_timeout_days: int
    approval_action_at_timeout: str
    approval_priority: str
    override_recipients: tuple[str, ...]
    cc_approver: bool
    owner_id: str | None
    plugin_alias: str
    plugin_display_name: str
    launcher_access_approval: str = "MANAGER"
    env_file: str | None = None
    part_size: int = LOOP_MAX
    temporary_enabled: bool = True
    temporary_allow: tuple[str, ...] = TEMPORARY_MODES
    temporary_units: tuple[str, ...] = tuple(DURATION_UNITS)
    temporary_max_days: int | None = None
    deploy_launcher: bool = True
    deploy_plugin: bool = True
    plugin_public: bool = False
    source_path: str | None = field(default=None, compare=False)
    # Old key names that were mapped to new ones; shown by `bulkaccess.py show-config`.
    deprecations: tuple[str, ...] = field(default=(), compare=False)

    # Every object we create is named from the prefix, so a tenant can host several
    # independent copies (and so other teams can tell ours apart).
    @property
    def base_name(self) -> str:
        return f"{self.prefix} Bulk Access Request".strip()

    @property
    def form_name(self) -> str:
        return f"{self.base_name} Form"

    @property
    def launcher_workflow_name(self) -> str:
        return self.base_name

    @property
    def plugin_workflow_name(self) -> str:
        return f"{self.base_name} (Plugin)"

    @property
    def launcher_name(self) -> str:
        return self.base_name

    @property
    def live(self) -> bool:
        return self.mode == "live"

    # ── what each deployment actually uses (derived; never configured twice) ──
    @property
    def deployments(self) -> tuple[str, ...]:
        return tuple(d for d, on in (("launcher", self.deploy_launcher), ("plugin", self.deploy_plugin)) if on)

    @property
    def launcher_people_cap(self) -> int:
        """The Launcher form's people picker: the config cap, but never above SailPoint's 30."""
        return min(self.people_max or FORM_SELECT_MAX, FORM_SELECT_MAX)

    @property
    def plugin_people_max(self) -> int | None:
        """The plugin's people cap; None means no limit (sent in parts of `part_size`)."""
        return self.people_max

    @property
    def launcher_temporary_modes(self) -> tuple[str, ...]:
        """The Launcher offers durations only: a workflow can't turn a form date into a duration."""
        return tuple(m for m in self.plugin_temporary_modes if m == "duration")

    @property
    def plugin_temporary_modes(self) -> tuple[str, ...]:
        return self.temporary_allow if self.temporary_enabled else ()


def _require(cond: bool, message: str) -> None:
    if not cond:
        raise ConfigError(message)


def _optional_int(value: Any, name: str, minimum: int) -> int | None:
    if value is None:
        return None
    _require(isinstance(value, int) and not isinstance(value, bool) and value >= minimum,
             f"`{name}` must be null (no limit) or a whole number of at least {minimum}.")
    return value


def from_dict(data: dict[str, Any], source_path: str | None = None) -> Config:
    inc = data.get("inc") or {}
    catalog = data.get("catalog") or {}
    people = data.get("people") or {}
    approval = data.get("approval") or {}
    notes = data.get("notifications") or {}
    plugin = data.get("plugin") or {}
    temporary = data.get("temporaryAccess") or {}
    deployments = data.get("deployments") or {}
    access = data.get("access") or {}
    deprecations: list[str] = []

    prefix = str(data.get("prefix", "")).strip()
    _require(bool(prefix), "`prefix` is required (e.g. \"ACME\"); it names every object created in the tenant.")
    _require(len(prefix) <= 20, "`prefix` must be 20 characters or fewer.")

    mode = data.get("mode", "dry-run")
    _require(mode in MODES, f"`mode` must be one of {MODES}, got {mode!r}.")

    unknown = set(deployments) - set(DEPLOYMENTS)
    _require(not unknown, f"`deployments` may only contain {DEPLOYMENTS}, got {sorted(unknown)}.")
    deploy_launcher = bool(deployments.get("launcher", True))
    deploy_plugin = bool(deployments.get("plugin", True))
    _require(deploy_launcher or deploy_plugin, "`deployments` must enable at least one of launcher or plugin.")

    pattern = inc.get("pattern", r"^INC\d{7}$")
    try:
        re.compile(pattern)
    except re.error as exc:
        raise ConfigError(f"`inc.pattern` is not a valid regular expression: {exc}") from exc
    example = inc.get("example", "INC0012345")
    _require(re.search(pattern, example) is not None, f"`inc.example` ({example!r}) does not match `inc.pattern`.")

    types = tuple(catalog.get("types") or ITEM_TYPES)
    _require(all(t in ITEM_TYPES for t in types), f"`catalog.types` may only contain {ITEM_TYPES}.")

    max_items = int(catalog.get("maxItems", 25))
    _require(1 <= max_items <= 25, "`catalog.maxItems` must be between 1 and 25 (SailPoint's per-request limit).")

    people_max = _optional_int(people.get("max"), "people.max", 1)
    part_size = people.get("partSize", LOOP_MAX)
    _require(isinstance(part_size, int) and not isinstance(part_size, bool) and 1 <= part_size <= LOOP_MAX,
             f"`people.partSize` must be between 1 and {LOOP_MAX} (SailPoint's workflow loop limit).")

    enabled = temporary.get("enabled", True)
    _require(isinstance(enabled, bool), "`temporaryAccess.enabled` must be true or false.")
    allow = tuple(temporary.get("allow") or TEMPORARY_MODES)
    _require(all(m in TEMPORARY_MODES for m in allow), f"`temporaryAccess.allow` may only contain {TEMPORARY_MODES}.")
    units = tuple(temporary.get("units") or DURATION_UNITS)
    _require(all(u in DURATION_UNITS for u in units), f"`temporaryAccess.units` may only contain {tuple(DURATION_UNITS)}.")
    max_days = _optional_int(temporary.get("maxDays"), "temporaryAccess.maxDays", 1)

    timeout = int(approval.get("timeoutDays", 7))
    _require(1 <= timeout <= 90, "`approval.timeoutDays` must be between 1 and 90.")
    at_timeout = approval.get("actionAtTimeout", "EXPIRED")
    _require(at_timeout in TIMEOUT_ACTIONS, f"`approval.actionAtTimeout` must be one of {TIMEOUT_ACTIONS}.")
    priority = approval.get("priority", "MEDIUM")
    _require(priority in PRIORITIES, f"`approval.priority` must be one of {PRIORITIES}.")

    recipients = tuple(notes.get("overrideRecipients") or ())
    _require(all("@" in r for r in recipients), "`notifications.overrideRecipients` must be email addresses.")

    access_approval = access.get("launcherApproval")
    old_launcher = data.get("launcher") or {}
    if "accessApproval" in old_launcher:
        deprecations.append("`launcher.accessApproval` is now `access.launcherApproval`.")
        if access_approval is None:
            access_approval = old_launcher["accessApproval"]
    access_approval = access_approval or "MANAGER"
    _require(access_approval in ("MANAGER", "NONE"), "`access.launcherApproval` must be \"MANAGER\" or \"NONE\".")

    alias = plugin.get("alias") or f"{prefix.lower()}-bulk-access"
    _require(re.fullmatch(r"[a-z0-9][a-z0-9-]{1,48}", alias) is not None,
             "`plugin.alias` must be lowercase letters, digits and dashes.")

    return Config(
        prefix=prefix,
        mode=mode,
        inc_pattern=pattern,
        inc_example=example,
        inc_message=inc.get("message") or f"Enter a ServiceNow incident number, e.g. {example}.",
        catalog_types=types,
        catalog_name_starts_with=catalog.get("nameStartsWith") or None,
        catalog_max_items=max_items,
        people_max=people_max,
        approval_timeout_days=timeout,
        approval_action_at_timeout=at_timeout,
        approval_priority=priority,
        override_recipients=recipients,
        cc_approver=bool(notes.get("ccApprover", True)),
        owner_id=data.get("owner") or None,
        plugin_alias=alias,
        plugin_display_name=plugin.get("displayName") or f"{prefix} Bulk Access Request",
        launcher_access_approval=access_approval,
        env_file=data.get("envFile") or None,
        part_size=part_size,
        temporary_enabled=enabled,
        temporary_allow=allow,
        temporary_units=units,
        temporary_max_days=max_days,
        deploy_launcher=deploy_launcher,
        deploy_plugin=deploy_plugin,
        plugin_public=bool(plugin.get("public", False)),
        source_path=source_path,
        deprecations=tuple(deprecations),
    )


def load(path: str | os.PathLike[str]) -> Config:
    p = Path(path)
    if not p.exists():
        raise ConfigError(
            f"Config file {p} not found. Copy config/bulk-access.example.json to config/<tenant>.json and edit it."
        )
    try:
        data = json.loads(p.read_text())
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{p} is not valid JSON: {exc}") from exc
    return from_dict(data, source_path=str(p))
