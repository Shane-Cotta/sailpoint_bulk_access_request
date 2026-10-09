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
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MODES = ("dry-run", "live")
ITEM_TYPES = ("ACCESS_PROFILE", "ROLE", "ENTITLEMENT")
TIMEOUT_ACTIONS = ("EXPIRED", "APPROVED")
PRIORITIES = ("LOW", "MEDIUM", "HIGH")
DEPLOYMENTS = ("launcher", "plugin")

# Printed by show-config and the installers when `owner` is null (a note, not an error: whether the PAT user is a
# person or a service identity can't be told offline).
OWNER_NOTE = ("Workflows are owned by the PAT user; requests show that identity as requester, and approvals it would get "
              "as an item owner/manager are escalated to an admin. Consider a dedicated service identity in `owner`.")

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

# The plugin's Approvals tab (item approvers decide one bulk request's approvals at once).
# `useBulkEndpoint`: "auto" uses generic-approvals/bulk-approve|reject only when the caller
# may; "always" / "never" force it on or off. Messages are mirrored in the plugin's
# runtime-config.ts, so they must match exactly.
BULK_ENDPOINT_MODES = ("auto", "always", "never")
APPROVALS_CONCURRENCY_MAX = 8
APPROVALS_MAX_ROWS_RANGE = (250, 20000)
MSG_APPROVALS_OBJECT = "`approvals` must be an object."
MSG_APPROVALS_CONCURRENCY = f"`approvals.concurrency` must be a whole number between 1 and {APPROVALS_CONCURRENCY_MAX}."
MSG_APPROVALS_BULK_ENDPOINT = '`approvals.useBulkEndpoint` must be "auto", "always" or "never".'
MSG_APPROVALS_MAX_ROWS = ("`approvals.maxRows` must be a whole number between "
                          f"{APPROVALS_MAX_ROWS_RANGE[0]} and {APPROVALS_MAX_ROWS_RANGE[1]}.")


# How the plugin submits a request (`plugin.submit`):
#  "launcher"      -- it starts the Launcher deployment's Launcher and submits its form as the signed-in user,
#                     so anyone with the Launcher Access profile can submit (verified live as a non-admin);
#  "test-endpoint" -- it starts its own disabled workflow through the workflow test endpoint (ORG_ADMIN only).
PLUGIN_SUBMIT_MODES = ("launcher", "test-endpoint")
MSG_PLUGIN_SUBMIT = '`plugin.submit` must be "launcher" or "test-endpoint".'
MSG_PLUGIN_SUBMIT_LAUNCHER = ('`plugin.submit` "launcher" needs the Launcher deployment: set `deployments.launcher` '
                              'to true, or use "test-endpoint".')


# Emails (`notifications`): links point at the tenant's UI, derived from the API host of SAIL_BASE_URL
# ("https://acme.api.identitynow.com" -> "https://acme.identitynow.com") unless `uiBaseUrl` overrides it.
# The URL is written into the email markup, so it may not carry characters the email template engine
# (Velocity) or HTML would read: no "#", "$", quotes, "<", ">" or whitespace.
UI_BASE_URL_RE = r"https?://[^\s#$\"'<>?]+"
MSG_UI_BASE_URL = ('`notifications.uiBaseUrl` must be null or an http(s) URL such as "https://acme.identitynow.com" '
                   '(no query string, "#", "$", quotes or spaces).')
HELP_CONTACT_MAX = 300
MSG_HELP_CONTACT = f"`notifications.helpContact` must be null or text of at most {HELP_CONTACT_MAX} characters."
MSG_PENDING_EMAIL = "`notifications.pendingEmail` must be true or false."


def msg_approvals_flag(key: str) -> str:
    return f"`approvals.{key}` must be true or false."


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
    plugin_submit: str = "test-endpoint"
    approvals_enabled: bool = True
    approvals_concurrency: int = 4
    approvals_use_bulk_endpoint: str = "auto"
    approvals_max_rows: int = 5000
    approvals_show_other: bool = False
    approvals_deny_comment_required: bool = True
    help_contact: str | None = None
    ui_base_url_override: str | None = None
    pending_email: bool = True
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
    def launcher_access_profile_name(self) -> str:
        """The requestable access profile that lets its holders use the Launcher (and, in launcher
        submit mode, submit from the plugin)."""
        return f"{self.base_name} - Launcher Access"

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
        return ("duration",) if self.temporary_enabled and "duration" in self.temporary_allow else ()

    @property
    def launcher_form_has_duration(self) -> bool:
        """Whether the Launcher form gets its duration fields: durations are offered and at least
        one configured unit fits `maxDays` once (definitions.launcher_duration_units)."""
        return "duration" in self.launcher_temporary_modes and any(
            _unit_fits(u, self.temporary_max_days) for u in self.temporary_units)

    @property
    def plugin_temporary_modes(self) -> tuple[str, ...]:
        """What the plugin offers. In launcher submit mode the request travels in the Launcher form,
        which carries temporary access only in its duration fields (an end date goes as hours), so
        without those fields the plugin offers permanent access only."""
        modes = self.temporary_allow if self.temporary_enabled else ()
        if self.plugin_submits_via_launcher and not self.launcher_form_has_duration:
            return ()
        return modes

    @property
    def plugin_submits_via_launcher(self) -> bool:
        """The plugin submits through the Launcher (as the signed-in user; no ORG_ADMIN needed)."""
        return self.deploy_plugin and self.plugin_submit == "launcher"

    @property
    def plugin_needs_workflow(self) -> bool:
        """The plugin's own (disabled) workflow is only installed for the test-endpoint mode."""
        return self.deploy_plugin and self.plugin_submit == "test-endpoint"

    @property
    def plugin_approvals_enabled(self) -> bool:
        """The Approvals tab lives in the plugin, so it needs the plugin deployment too."""
        return self.deploy_plugin and self.approvals_enabled


def ui_base_url(cfg: Config, api_base_url: str | None) -> str | None:
    """The tenant's UI address for email links: `notifications.uiBaseUrl`, else derived from the API host
    (".api." dropped: acme.api.identitynow.com -> acme.identitynow.com, and the same for identitynow-demo.com).
    None when neither gives one (the emails then name the pages without linking them)."""
    if cfg.ui_base_url_override:
        return cfg.ui_base_url_override
    if not api_base_url:
        return None
    parts = urllib.parse.urlsplit(api_base_url.strip())
    host = parts.hostname or ""
    labels = host.split(".")
    if parts.scheme not in ("http", "https") or "api" not in labels[1:-1]:
        return None
    ui_host = ".".join(label for i, label in enumerate(labels) if not (label == "api" and i > 0))
    url = f"{parts.scheme}://{ui_host}" + (f":{parts.port}" if parts.port else "")
    return url if re.fullmatch(UI_BASE_URL_RE, url) else None


def _unit_fits(unit: str, max_days: int | None) -> bool:
    """At least one `unit` fits in `max_days` (rules.unit_max_count != 0)."""
    return max_days is None or max_days / UNIT_MAX_DAYS[unit] + 1e-9 >= 1


def _require(cond: bool, message: str) -> None:
    if not cond:
        raise ConfigError(message)


def _optional_int(value: Any, name: str, minimum: int) -> int | None:
    if value is None:
        return None
    _require(isinstance(value, int) and not isinstance(value, bool) and value >= minimum,
             f"`{name}` must be null (no limit) or a whole number of at least {minimum}.")
    return value


def _whole(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _approvals(raw: Any) -> dict[str, Any]:
    """The `approvals` block, validated, with defaults for missing keys."""
    if raw is None:
        raw = {}
    _require(isinstance(raw, dict), MSG_APPROVALS_OBJECT)
    out: dict[str, Any] = {}
    for key, default in (("enabled", True), ("showOther", False), ("denyCommentRequired", True)):
        value = raw.get(key, default)
        _require(isinstance(value, bool), msg_approvals_flag(key))
        out[key] = value
    concurrency = raw.get("concurrency", 4)
    _require(_whole(concurrency) and 1 <= concurrency <= APPROVALS_CONCURRENCY_MAX, MSG_APPROVALS_CONCURRENCY)
    endpoint = raw.get("useBulkEndpoint", "auto")
    _require(endpoint in BULK_ENDPOINT_MODES, MSG_APPROVALS_BULK_ENDPOINT)
    max_rows = raw.get("maxRows", 5000)
    low, high = APPROVALS_MAX_ROWS_RANGE
    _require(_whole(max_rows) and low <= max_rows <= high, MSG_APPROVALS_MAX_ROWS)
    out.update(concurrency=concurrency, useBulkEndpoint=endpoint, maxRows=max_rows)
    return out


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
    ui_url = notes.get("uiBaseUrl")
    if isinstance(ui_url, str):
        ui_url = ui_url.strip().rstrip("/") or None
    _require(ui_url is None or (isinstance(ui_url, str) and re.fullmatch(UI_BASE_URL_RE, ui_url) is not None),
             MSG_UI_BASE_URL)
    help_contact = notes.get("helpContact")
    if isinstance(help_contact, str):
        help_contact = " ".join(help_contact.split()) or None
    _require(help_contact is None or (isinstance(help_contact, str) and len(help_contact) <= HELP_CONTACT_MAX),
             MSG_HELP_CONTACT)
    pending_email = notes.get("pendingEmail", True)
    _require(isinstance(pending_email, bool), MSG_PENDING_EMAIL)

    access_approval = access.get("launcherApproval")
    old_launcher = data.get("launcher") or {}
    if "accessApproval" in old_launcher:
        deprecations.append("`launcher.accessApproval` is now `access.launcherApproval`.")
        if access_approval is None:
            access_approval = old_launcher["accessApproval"]
    access_approval = access_approval or "MANAGER"
    _require(access_approval in ("MANAGER", "NONE"), "`access.launcherApproval` must be \"MANAGER\" or \"NONE\".")

    approvals = _approvals(data.get("approvals"))

    submit = plugin.get("submit")
    if submit is None:
        submit = "launcher" if deploy_launcher else "test-endpoint"
    _require(submit in PLUGIN_SUBMIT_MODES, MSG_PLUGIN_SUBMIT)
    _require(submit != "launcher" or deploy_launcher, MSG_PLUGIN_SUBMIT_LAUNCHER)

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
        plugin_submit=submit,
        approvals_enabled=approvals["enabled"],
        approvals_concurrency=approvals["concurrency"],
        approvals_use_bulk_endpoint=approvals["useBulkEndpoint"],
        approvals_max_rows=approvals["maxRows"],
        approvals_show_other=approvals["showOther"],
        approvals_deny_comment_required=approvals["denyCommentRequired"],
        help_contact=help_contact,
        ui_base_url_override=ui_url,
        pending_email=pending_email,
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
