"""Pure business rules shared by both deployments (and mirrored in the plugin).

The plugin's `src/app/bulk/rules.ts` mirrors this file; problem messages must match
exactly in both languages (see docs/dev/CONTRACTS.md section 2).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timezone, tzinfo
from typing import Any, Iterable, Sequence

from .config import DURATION_UNITS, LOOP_MAX, UNIT_MAX_DAYS, Config

TYPE_LABELS = {"ACCESS_PROFILE": "Access profile", "ROLE": "Role", "ENTITLEMENT": "Entitlement"}

# The justification travels in every access request's comment. SailPoint accepts long comments (4,000 characters
# verified live); this keeps them readable for the item approvers.
JUSTIFICATION_MAX = 1000
MSG_JUSTIFICATION = "Enter a business justification."
MSG_JUSTIFICATION_LONG = f"Keep the justification to {JUSTIFICATION_MAX} characters or fewer."

# Shown next to catalog items whose own approval setup has no approver (the installer works it out:
# no_approval_ids): SailPoint grants them as soon as they're requested.
NO_APPROVAL_LABEL = "No approval: granted right away"


def inc_is_valid(cfg: Config, value: str | None) -> bool:
    return bool(value) and re.search(cfg.inc_pattern, value.strip()) is not None


def validate_request(cfg: Config, *, people: list[str], items: list[dict[str, Any]], inc: str,
                     justification: str) -> list[str]:
    """Everything wrong with a bulk request, as sentences (empty list = OK). Each item is then approved the way
    SailPoint has it set up; there is no approval for the request as a whole."""
    problems = []
    if not people:
        problems.append("Choose at least one person.")
    if cfg.people_max is not None and len(set(people)) > cfg.people_max:
        problems.append(f"Choose at most {cfg.people_max} people.")
    if not items:
        problems.append("Choose at least one access item.")
    if len(items) > cfg.catalog_max_items:
        problems.append(f"Choose at most {cfg.catalog_max_items} access items.")
    if any(i.get("type") not in cfg.catalog_types for i in items):
        problems.append("One of the chosen items is not an allowed type.")
    if not inc_is_valid(cfg, inc):
        problems.append(cfg.inc_message)
    text = (justification or "").strip()
    if not text:
        problems.append(MSG_JUSTIFICATION)
    elif len(text) > JUSTIFICATION_MAX:
        problems.append(MSG_JUSTIFICATION_LONG)
    return problems


def clip(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ── where the catalog comes from (CONTRACTS §8) ──────────────────────────────
# /requestable-objects only lists access profiles and roles: ENTITLEMENT is outside its
# `types` enum (alone -> 400; repeated next to another type -> silently dropped). Requestable
# entitlements come from /entitlements?filters=requestable eq true instead.
REQUESTABLE_OBJECT_TYPES = ("ACCESS_PROFILE", "ROLE")


def requestable_object_types(cfg: Config) -> list[str]:
    """The configured types that /requestable-objects can list (empty: don't call it)."""
    return [t for t in cfg.catalog_types if t in REQUESTABLE_OBJECT_TYPES]


def filter_quote(value: str) -> str:
    """A string literal for a SailPoint `filters` expression."""
    return '"' + re.sub(r'(["\\])', r"\\\1", value) + '"'


def entitlement_filter(cfg: Config) -> str | None:
    """The /entitlements filter for the catalog's entitlements (None: not offered)."""
    if "ENTITLEMENT" not in cfg.catalog_types:
        return None
    prefix = cfg.catalog_name_starts_with
    return "requestable eq true" + (f" and name sw {filter_quote(prefix)}" if prefix else "")


def no_approval_ids(objects: Iterable[dict[str, Any]], entitlement_default: Any) -> set[str]:
    """The IDs of the items SailPoint grants without any approval (CLAUDE.md: verified live).

    `objects` carry `type` and their own `approvalSchemes`: an access profile or role with none is granted at once.
    An entitlement with none falls back to the tenant's `entitlementRequestConfig.grantRequestApprovalSchemes`
    (`entitlement_default`, e.g. "sourceOwner"), so it only goes without approval when that is empty too.
    """
    default_empty = not entitlement_default or str(entitlement_default).strip().lower() in ("none", "null")
    out = set()
    for obj in objects:
        if obj.get("approvalSchemes"):
            continue
        if obj.get("type") != "ENTITLEMENT" or default_empty:
            out.add(obj["id"])
    return out


def catalog_options(cfg: Config, requestable: Iterable[dict[str, Any]],
                    no_approval: Iterable[str] = ()) -> list[dict[str, Any]]:
    """Form SELECT options from /requestable-objects and requestable entitlements, filtered by the config.

    Entitlement rows (from /entitlements) carry no `type`; the caller tags them "ENTITLEMENT". Items in
    `no_approval` (no_approval_ids) say so in their sub-label, so whoever picks them sees they're granted at once.

    Each option's value is the complete access object ({id, type, name}), which is
    exactly what the workflow's Manage Access step needs -- so the workflow never has
    to rebuild objects from bare IDs.
    """
    options = []
    granted_at_once = set(no_approval)
    own = cfg.launcher_access_profile_name   # the profile that grants this tool; never offer it
    for obj in requestable:
        if obj.get("name") == own:
            continue
        kind = obj.get("type")
        name = obj.get("name") or obj.get("id")
        if kind not in cfg.catalog_types:
            continue
        if cfg.catalog_name_starts_with and not str(name).startswith(cfg.catalog_name_starts_with):
            continue
        source = (obj.get("source") or {}).get("name") if isinstance(obj.get("source"), dict) else None
        sub = TYPE_LABELS.get(kind, kind) + (f" · {source}" if source else "")
        if obj["id"] in granted_at_once:
            sub += f" · {NO_APPROVAL_LABEL}"
        options.append({"label": name, "subLabel": sub, "value": {"id": obj["id"], "type": kind, "name": name}})
    return sorted(options, key=lambda o: (o["label"].lower(), o["value"]["type"]))


# ── the item comment (CONTRACTS §4) ───────────────────────────────────────────
# Every access request a bulk request files carries one comment (the Launcher workflow builds it in definitions.py,
# the plugin in rules.ts bulkComment):
#   "<INC> | Bulk access request by <requester> | <access label> | <justification>"
# Requests filed before the bulk approver was removed carry one more field, "Approved by <approver>", after the
# requester; they are still read (approver set), so approvals already pending keep their INC grouping.
COMMENT_SEPARATOR = " | "
REQUESTER_LABEL = "Bulk access request by"

# Three fields without "|", then the justification (everything after the 3rd separator, "|" allowed); the old form
# has four. Written to behave the same in Python and JavaScript (rules.ts uses the same patterns).
_BULK_COMMENT = re.compile(r"^\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*([\s\S]*?)\s*$")
_OLD_BULK_COMMENT = re.compile(r"^\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*([\s\S]*?)\s*$")
_REQUESTER = re.compile(r"^Bulk\s+access\s+request\s+by\s+(\S[\s\S]*)$")
_OLD_APPROVER_LABEL = re.compile(r"^Approved\s+by\b")
_OLD_APPROVER = re.compile(r"^Approved\s+by\s+(\S[\s\S]*)$")


def bulk_comment(inc: str, requester: str, access_label: str, justification: str) -> str:
    """The comment every access request of a bulk request carries (parse_bulk_comment reads it back)."""
    return COMMENT_SEPARATOR.join([inc.strip(), f"{REQUESTER_LABEL} {requester}", access_label, justification.strip()])


def parse_bulk_comment(cfg: Config, text: Any) -> dict[str, Any] | None:
    """The parts of a bulk request's item comment, or None for any other comment.

    Returns {inc, requester, approver, accessLabel, justification}; `approver` is None except on comments written
    while bulk requests had an approver ("… | Approved by <approver> | …"). The INC must match the configured
    pattern; requester and access label must not be empty; the justification may be empty and may itself contain
    " | ". Extra whitespace around the separators and between the label words is tolerated.
    """
    if not isinstance(text, str):
        return None
    old = _OLD_BULK_COMMENT.match(text)
    if old and _OLD_APPROVER_LABEL.match(old.group(3)):    # the old form: its approver must be named
        inc, by, approved, label, justification = old.groups()
        named = _OLD_APPROVER.match(approved)
        if not named:
            return None
        approver: str | None = named.group(1)
    else:
        m = _BULK_COMMENT.match(text)
        if not m:
            return None
        inc, by, label, justification = m.groups()
        approver = None
    requester = _REQUESTER.match(by)
    if not (inc_is_valid(cfg, inc) and requester and label):
        return None
    return {"inc": inc, "requester": requester.group(1), "approver": approver,
            "accessLabel": label, "justification": justification}


# ── people in parts ───────────────────────────────────────────────────────────
def split_into_parts(people: Iterable[str], part_size: int = LOOP_MAX) -> list[list[str]]:
    """The people, deduplicated in order, in chunks of `part_size`. Through the Launcher each chunk is one workflow
    run (SailPoint's loop takes at most 250 items); the plugin files direct requests 10 people at a time."""
    if not isinstance(part_size, int) or isinstance(part_size, bool) or part_size < 1:
        raise ValueError("part_size must be a whole number of at least 1.")
    unique = list(dict.fromkeys(people))
    return [unique[i:i + part_size] for i in range(0, len(unique), part_size)]


def part_label(i: int, n: int) -> str:
    """" (2/3)" for part 2 of 3 (1-based, leading space); "" when there is only one part."""
    return "" if n == 1 else f" ({i}/{n})"


# ── temporary access ──────────────────────────────────────────────────────────
PERMANENT, DURATION = "permanent", "duration"
ROUTES = ("launcher", "plugin")
UNIT_WORDS = {"HOURS": "hour", "DAYS": "day", "WEEKS": "week", "MONTHS": "month"}

MSG_TEMPORARY_UNAVAILABLE = "Temporary access isn't available."
MSG_DURATION_NUMBER = "Enter the duration as a whole number of 1 or more."
MSG_DURATION_UNIT = "Choose a unit for the duration."
MSG_END_DATE = "Choose an end date after today."


def msg_max_days(max_days: int) -> str:
    return f"Temporary access can last at most {max_days} days."


@dataclass(frozen=True)
class AccessChoice:
    """What a request carries: the Manage Access `removeDuration` ("" = permanent) and its label."""
    remove_duration: str
    access_label: str


PERMANENT_ACCESS = AccessChoice("", "Permanent")


def route_temporary_modes(cfg: Config, route: str) -> tuple[str, ...]:
    if route not in ROUTES:
        raise ValueError(f"route must be one of {ROUTES}")
    return cfg.launcher_temporary_modes if route == "launcher" else cfg.plugin_temporary_modes


def whole_number(value: Any) -> int | None:
    """`value` as a whole number >= 1, or None. Accepts ints and digit strings (a form TEXT field
    arrives as a string); rejects booleans, fractions and anything else."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        n = value
    elif isinstance(value, float) and value.is_integer():
        n = int(value)
    elif isinstance(value, str) and re.fullmatch(r"[0-9]+", value.strip()):
        n = int(value.strip())
    else:
        return None
    return n if n >= 1 else None


def duration_label(n: int, unit: str) -> str:
    """"Temporary: 1 day", "Temporary: 30 days", "Temporary: 2 hours"."""
    word = UNIT_WORDS[unit]
    return f"Temporary: {n} {word}{'' if n == 1 else 's'}"


def _local(dt: datetime, tz: tzinfo | None) -> datetime:
    """An aware datetime in `tz` (default: this machine's local zone); naive input is taken as local."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz) if tz else dt.astimezone()
    return dt.astimezone(tz) if tz else dt.astimezone()


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()):
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            return None
    return None


def end_date_hours(end_date: date | str, *, now: datetime | None = None, tz: tzinfo | None = None) -> int:
    """Hours from `now` to the end of `end_date` (23:59:59 local time in `tz`), rounded up.

    Manage Access only takes durations, so the plugin turns an end date into "<hours>h".
    """
    d = _parse_date(end_date)
    if d is None:
        raise ValueError(MSG_END_DATE)
    now = _local(now or datetime.now(tz), tz)
    naive_end = datetime.combine(d, time(23, 59, 59))
    end = naive_end.replace(tzinfo=tz) if tz else naive_end.astimezone()   # astimezone(): DST-correct local time
    # Subtract in UTC: Python subtracts two datetimes sharing a tzinfo on wall-clock time, ignoring DST changes.
    return math.ceil((end.astimezone(timezone.utc) - now.astimezone(timezone.utc)).total_seconds() / 3600)


def _resolve_access(cfg: Config, mode: str, n: Any, unit: Any, end_date: Any, route: str,
                    now: datetime | None, today: date | None, tz: tzinfo | None) -> tuple[AccessChoice | None, list[str]]:
    if mode == PERMANENT:
        return PERMANENT_ACCESS, []
    if mode not in route_temporary_modes(cfg, route):
        return None, [MSG_TEMPORARY_UNAVAILABLE]
    max_days = cfg.temporary_max_days
    if mode == DURATION:
        problems = []
        count = whole_number(n)
        if count is None:
            problems.append(MSG_DURATION_NUMBER)
        if unit not in cfg.temporary_units:
            problems.append(MSG_DURATION_UNIT)
        if problems:
            return None, problems
        if max_days is not None and count * UNIT_MAX_DAYS[unit] > max_days:
            return None, [msg_max_days(max_days)]
        return AccessChoice(f"{count}{DURATION_UNITS[unit]}", duration_label(count, unit)), []
    # endDate
    d = _parse_date(end_date)
    now = _local(now or datetime.now(tz), tz)
    today = today or now.date()
    if d is None or d <= today:
        return None, [MSG_END_DATE]
    hours = end_date_hours(d, now=now, tz=tz)
    if max_days is not None and hours / 24 > max_days:
        return None, [msg_max_days(max_days)]
    return AccessChoice(f"{hours}h", f"Temporary: until {d.isoformat()}"), []


def validate_access(cfg: Config, mode: str, *, n: Any = None, unit: Any = None, end_date: Any = None,
                    route: str = "plugin", now: datetime | None = None, today: date | None = None,
                    tz: tzinfo | None = None) -> list[str]:
    """Everything wrong with an access choice (empty list = OK).

    mode: "permanent", "duration" (with `n` and `unit`, e.g. 30 and "DAYS") or "endDate"
    (with `end_date`, "YYYY-MM-DD"; plugin only). `now` / `today` / `tz` are injectable for tests;
    the end date is read in `tz`, defaulting to this machine's local zone.
    """
    return _resolve_access(cfg, mode, n, unit, end_date, route, now, today, tz)[1]


def access_choice(cfg: Config, mode: str, *, n: Any = None, unit: Any = None, end_date: Any = None,
                  route: str = "plugin", now: datetime | None = None, today: date | None = None,
                  tz: tzinfo | None = None) -> AccessChoice:
    """The `removeDuration` and label for a valid choice; ValueError (first problem) otherwise."""
    choice, problems = _resolve_access(cfg, mode, n, unit, end_date, route, now, today, tz)
    if problems or choice is None:
        raise ValueError(problems[0] if problems else MSG_TEMPORARY_UNAVAILABLE)
    return choice


_REMOVE_DURATION = re.compile(r"([1-9][0-9]*)([hdwM])")


def launcher_form_access(remove_duration: str) -> dict[str, Any]:
    """The Launcher form's access fields for a validated `removeDuration`: what the plugin sends when it
    submits through the Launcher (CONTRACTS §9). "" -> permanent; "30d" -> temporary, duration "30", unit
    ["d"] (the unit SELECT's option values are the duration suffixes). An end date arrives as hours ("720h")."""
    if remove_duration == "":
        return {"accessType": False, "duration": "", "durationUnit": []}
    m = _REMOVE_DURATION.fullmatch(remove_duration or "")
    if not m:
        raise ValueError(f"Not a duration: {remove_duration!r}")
    return {"accessType": True, "duration": m.group(1), "durationUnit": [m.group(2)]}


# ── regexes the workflows use (they can't do arithmetic) ──────────────────────
def int_range_regex(maximum: int) -> str:
    """A regex alternation (no anchors) matching the whole numbers 1..maximum, without leading zeros.
    RE2-compatible, so SailPoint's StringMatches can enforce `maxDays` on a duration string."""
    if maximum < 1:
        raise ValueError("maximum must be at least 1")
    top = str(maximum)
    alts = []
    for width in range(1, len(top)):            # every shorter number
        alts.append("[1-9]" + (f"[0-9]{{{width - 1}}}" if width > 1 else ""))
    for i, ch in enumerate(top):                  # same width, smaller at position i
        low, high = (1 if i == 0 else 0), int(ch) - 1
        if high >= low:
            rest = len(top) - i - 1
            digit = str(low) if low == high else f"[{low}-{high}]"
            alts.append(top[:i] + digit + (f"[0-9]{{{rest}}}" if rest else ""))
    alts.append(top)
    return "|".join(alts)


def unit_max_count(unit: str, max_days: int | None) -> int | None:
    """How many of `unit` fit in `max_days` (None = no limit). 0 means the unit is unusable."""
    if max_days is None:
        return None
    return math.floor(max_days / UNIT_MAX_DAYS[unit] + 1e-9)


def duration_regex(units: Sequence[str], max_days: int | None, *, allow_empty: bool = False) -> str:
    """Anchored regex for a valid `removeDuration` string ("30d") in the given units, within max_days."""
    alts = []
    for unit in units:
        cap = unit_max_count(unit, max_days)
        if cap == 0:
            continue
        number = "[1-9][0-9]*" if cap is None else f"(?:{int_range_regex(cap)})"
        alts.append(number + DURATION_UNITS[unit])
    body = "|".join(alts)
    if not body:
        return "^$" if allow_empty else "^(?!)$"
    return f"^(?:{body}){'?' if allow_empty else ''}$"
