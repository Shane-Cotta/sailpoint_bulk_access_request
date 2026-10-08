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

# SailPoint limits on a generic approval task's text fields.
APPROVAL_NAME_MAX = 50
APPROVAL_DESCRIPTION_MAX = 150
APPROVAL_COMMENT_MAX = 150
# Soft limit the plugin applies for a tidy approval comment ("<INC>: <justification>").
# Not enforced by the Launcher form: a MAX_LENGTH rule on a form textarea breaks submission,
# and workflow-created approvals accept longer comments (225 characters verified live).
JUSTIFICATION_MAX = APPROVAL_COMMENT_MAX - len("INC0000000: ")   # 138


def inc_is_valid(cfg: Config, value: str | None) -> bool:
    return bool(value) and re.search(cfg.inc_pattern, value.strip()) is not None


def validate_request(cfg: Config, *, requester_id: str, approver_id: str, people: list[str],
                     items: list[dict[str, Any]], inc: str) -> list[str]:
    """Everything wrong with a bulk request, as sentences (empty list = OK)."""
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
    if not approver_id:
        problems.append("Choose an approver.")
    elif approver_id == requester_id:
        # SailPoint reassigns self-approvals to another admin, so refuse up front.
        problems.append("The approver must be someone other than you.")
    elif approver_id in people:
        # Nobody approves access for themselves, even inside a bulk request.
        problems.append(MSG_APPROVER_IN_PEOPLE)
    if not inc_is_valid(cfg, inc):
        problems.append(cfg.inc_message)
    return problems


def clip(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def catalog_options(cfg: Config, requestable: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Form SELECT options from /v3/requestable-objects, filtered by the config.

    Each option's value is the complete access object ({id, type, name}), which is
    exactly what the workflow's Manage Access step needs -- so the workflow never has
    to rebuild objects from bare IDs.
    """
    options = []
    own = f"{cfg.base_name} - Launcher Access"   # the profile that grants this tool; never offer it
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
        options.append({"label": name, "subLabel": sub, "value": {"id": obj["id"], "type": kind, "name": name}})
    return sorted(options, key=lambda o: (o["label"].lower(), o["value"]["type"]))


# ── the item comment (CONTRACTS §4) ───────────────────────────────────────────
# Every access request the workflow submits carries one comment, built by definitions.py:
#   "<INC> | Bulk access request by <requester> | Approved by <approver> | <access label> | <justification>"
COMMENT_SEPARATOR = " | "
REQUESTER_LABEL = "Bulk access request by"
APPROVER_LABEL = "Approved by"

# Four fields without "|", then the justification (everything after the 4th separator, "|" allowed).
# Written to behave the same in Python and JavaScript (rules.ts uses the same patterns).
_BULK_COMMENT = re.compile(r"^\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*([\s\S]*?)\s*$")
_REQUESTER = re.compile(r"^Bulk\s+access\s+request\s+by\s+(\S[\s\S]*)$")
_APPROVER = re.compile(r"^Approved\s+by\s+(\S[\s\S]*)$")


def parse_bulk_comment(cfg: Config, text: Any) -> dict[str, str] | None:
    """The parts of a bulk request's item comment, or None for any other comment.

    Returns {inc, requester, approver, accessLabel, justification}. The INC must match the
    configured pattern; requester, approver and access label must not be empty; the
    justification may be empty and may itself contain " | ". Extra whitespace around the
    separators and between the label words is tolerated.
    """
    if not isinstance(text, str):
        return None
    m = _BULK_COMMENT.match(text)
    if not m:
        return None
    inc, by, approved, label, justification = m.groups()
    requester = _REQUESTER.match(by)
    approver = _APPROVER.match(approved)
    if not (inc_is_valid(cfg, inc) and requester and approver and label):
        return None
    return {"inc": inc, "requester": requester.group(1), "approver": approver.group(1),
            "accessLabel": label, "justification": justification}


# ── people in parts ───────────────────────────────────────────────────────────
def split_into_parts(people: Iterable[str], part_size: int = LOOP_MAX) -> list[list[str]]:
    """The people, deduplicated in order, in chunks of `part_size` (one workflow run and
    one approval per chunk; SailPoint's loop takes at most 250 items)."""
    if not isinstance(part_size, int) or isinstance(part_size, bool) or part_size < 1:
        raise ValueError("part_size must be a whole number of at least 1.")
    unique = list(dict.fromkeys(people))
    return [unique[i:i + part_size] for i in range(0, len(unique), part_size)]


def part_label(i: int, n: int) -> str:
    """" (2/3)" for part 2 of 3 (1-based, leading space); "" when there is only one part."""
    return "" if n == 1 else f" ({i}/{n})"


# ── temporary access ──────────────────────────────────────────────────────────
PERMANENT, DURATION, END_DATE = "permanent", "duration", "endDate"
ACCESS_MODES = (PERMANENT, DURATION, END_DATE)
ROUTES = ("launcher", "plugin")
UNIT_WORDS = {"HOURS": "hour", "DAYS": "day", "WEEKS": "week", "MONTHS": "month"}

MSG_APPROVER_IN_PEOPLE = "The approver can't be one of the people getting the access."
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
    # END_DATE
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
