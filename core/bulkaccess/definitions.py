"""Pure builders for the SailPoint objects the Launcher deployment installs.

Nothing here calls the API: given a Config (and IDs the installer looked up),
each function returns the exact JSON body to send. That keeps the definitions
unit-testable and lets `install.py --dry-run` print what would be created.

The workflow (`bulk_workflow`) is started by a Launcher in the Launchpad. An
Interactive Form collects the request from whoever launched it; the workflow
checks it and files one access request per person, each with every chosen item.
There is no approval for the bulk request itself: each item is approved the way
SailPoint has it set up (its owner, the person's manager, ...; or not at all).
Results are reported back as Interactive Messages and email. The UI plugin
either files the requests itself ("direct", no workflow) or fills in this
Launcher's form as the signed-in user ("launcher", adding a `partLabel`
key to the form data when a request is sent in parts; the form has no such field).

Everything below was confirmed against a live tenant (see INSTALL.md
"How it works"): REGEX validation shape, STATIC options carrying full access
objects, and Manage Access inside one loop over the people (SailPoint rejects
nested loops and caps a request at 10 recipients, so one request per person --
each with all the chosen items -- has no limits).
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from typing import Any

from .config import DURATION_UNITS, Config
from .rules import (COMMENT_SEPARATOR, MSG_DURATION_NUMBER, MSG_DURATION_UNIT, REQUESTER_LABEL, duration_regex,
                    msg_max_days, unit_max_count)

# Form field keys.
F_PEOPLE, F_ITEMS, F_INC, F_JUSTIFICATION = "people", "items", "inc", "justification"
# Launcher form: temporary access (only when the config offers durations on the Launcher).
F_ACCESS_TYPE, F_DURATION, F_DURATION_UNIT = "accessType", "duration", "durationUnit"
UNIT_OPTION_LABELS = {"HOURS": "Hours", "DAYS": "Days", "WEEKS": "Weeks", "MONTHS": "Months"}
# Launcher form: hidden, set only by the plugin (launcher submit mode) when a request goes out in parts.
F_PART_LABEL = "partLabel"
# What the Launcher workflow accepts as a part label (rules.part_label: " (2/3)"); anything else means one part.
PART_LABEL_REGEX = r"^ \([1-9][0-9]*/[1-9][0-9]*\)$"


def launcher_duration_units(cfg: Config) -> tuple[str, ...]:
    """Units the Launcher form offers: the configured ones that fit `maxDays` at least once
    (with maxDays 5, a week can never be chosen, so it isn't offered)."""
    if "duration" not in cfg.launcher_temporary_modes:
        return ()
    return tuple(u for u in cfg.temporary_units if unit_max_count(u, cfg.temporary_max_days) != 0)


def launcher_offers_temporary(cfg: Config) -> bool:
    return bool(launcher_duration_units(cfg))


def launcher_workflow_units(cfg: Config) -> tuple[str, ...]:
    """Units the Launcher workflow accepts: the form's, plus hours when the plugin submits through
    the Launcher and offers an end date (it sends the end date as hours in the same fields)."""
    units = launcher_duration_units(cfg)
    if units and "HOURS" not in units and cfg.plugin_submits_via_launcher and "endDate" in cfg.plugin_temporary_modes:
        units = ("HOURS",) + units
    return units


def _owner(owner_id: str, owner_name: str | None = None) -> dict[str, Any]:
    return {"type": "IDENTITY", "id": owner_id, **({"name": owner_name} if owner_name else {})}


# ───────────────────────────────────────────────────────────────── form ──
def bulk_form(cfg: Config, owner_id: str, options: list[dict[str, Any]]) -> dict[str, Any]:
    """The intake form shown in the Launchpad. `options` come from rules.catalog_options (items granted without any
    approval say so in their sub-label)."""
    required = [{"validationType": "REQUIRED"}]
    elements = [
        {"id": "people", "key": F_PEOPLE, "elementType": "SELECT", "validations": required,
         "config": {"label": "People who need the access", "maximum": cfg.launcher_people_cap, "forceSelect": True,
                    "helpText": f"Search and add up to {cfg.launcher_people_cap} people.",
                    "dataSource": {"dataSourceType": "INTERNAL", "config": {"objectType": "IDENTITY"}}}},
        {"id": "items", "key": F_ITEMS, "elementType": "SELECT", "validations": required,
         "config": {"label": "Access to request", "maximum": cfg.catalog_max_items, "forceSelect": True,
                    "helpText": "Items from the Request Center catalog. Everyone above gets every item chosen here, "
                                "and each item is approved the way it's set up in SailPoint.",
                    "dataSource": {"dataSourceType": "STATIC", "config": {"options": options}}}},
        {"id": "inc", "key": F_INC, "elementType": "TEXT",
         "validations": required + [{"validationType": "REGEX",
                                     "config": {"regex": cfg.inc_pattern, "message": cfg.inc_message}}],
         "config": {"label": "ServiceNow incident (INC) number", "placeholder": cfg.inc_example,
                    "helpText": cfg.inc_message}},
        # No MAX_LENGTH here: on a TEXTAREA it silently stops the submission from reaching the
        # workflow (verified live), and access requests accept long comments anyway.
        {"id": "justification", "key": F_JUSTIFICATION, "elementType": "TEXTAREA", "validations": required,
         "config": {"label": "Business justification", "rows": 3,
                    "helpText": "Stored on every access request, where the item approvers see it."}},
        # No field for F_PART_LABEL: a HIDDEN element makes the Launchpad render an empty form (verified live).
        # The plugin (launcher submit mode) adds it to the form data anyway (the API takes keys the form doesn't
        # have) when a request goes out in parts; the workflow checks it (PART_LABEL_REGEX).
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
            # Not REQUIRED: hidden while Permanent. The workflow checks it again before requesting anything.
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
        "description": f"{cfg.prefix}: request access for several people at once, tracked by a ServiceNow INC number. "
                       "Each item is approved the way it's set up in SailPoint.",
        "owner": _owner(owner_id),
        "formInput": [],
        "formElements": [{"id": "section", "elementType": "SECTION",
                          "config": {"label": "Bulk access request", "formElements": elements}}],
        "formConditions": conditions,
    }


# ───────────────────────────────────────────────────────────── workflow ──
# The workflow keeps its access choice in variables. SailPoint only lets "Update Variable"
# change variables of a step whose name starts with "Define Variable" (verified live), and a
# variable can't be defined as a literal "" -- so it is defined as "permanent" and a replace
# transform empties it.
DEFINE_ACCESS = "Define Variable Access"
ACCESS_VARS = "$.defineVariableAccess"

_FORM = "$.interactiveForm.formData"
# Where each value lives in a run.
PATHS = {"people": f"{_FORM}.{F_PEOPLE}", "items": f"{_FORM}.{F_ITEMS}", "inc": f"{_FORM}.{F_INC}",
         "justification": f"{_FORM}.{F_JUSTIFICATION}", "requester": "$.trigger.launchedBy.id",
         "accessType": f"{_FORM}.{F_ACCESS_TYPE}", "duration": f"{_FORM}.{F_DURATION}",
         "durationUnit": f"{_FORM}.{F_DURATION_UNIT}", "partLabelInput": f"{_FORM}.{F_PART_LABEL}",
         "partLabel": f"{ACCESS_VARS}.partLabel",
         "removeDuration": f"{ACCESS_VARS}.removeDuration", "accessLabel": f"{ACCESS_VARS}.accessLabel"}


def _t(path: str) -> str:
    """Template a JSONPath into a string attribute: "{{$.x}}"."""
    return "{{" + path + "}}"


def _success() -> dict[str, Any]:
    return {"actionId": "sp:operator-success", "type": "success", "displayName": ""}


def _failure() -> dict[str, Any]:
    # Failure end steps carry failureName/description at the top level (validator error e300 otherwise).
    return {"actionId": "sp:operator-failure", "type": "failure", "displayName": "",
            "failureName": "Bulk request rejected",
            "description": "Stopped before anything was requested: the INC number or the temporary access duration "
                           "was invalid."}


# ─────────────────────────────────────────────────────────────── emails ──
# Verified live (send-email v2, 2026-10-09; docs/dev/CONTRACTS.md section 10):
# * The body goes to SailPoint's notification service as a Velocity template, and `context` holds its
#   variables. A body that isn't valid Velocity (e.g. a justification containing "#if(" templated in) is
#   refused with "400 Bad Request" and FAILS the run. So the body here is constant markup, and every value --
#   the requester's text, names, config text -- goes in `context`, whose values are data and never parsed.
# * Context values are JSONPath ("key.$"), never "{{$.x}}" templates: a template is spliced into the request
#   as raw text, so a value with a newline, a quote or a backslash (any multi-line justification) makes it
#   invalid and fails the run with the same 400. A "key.$" value travels intact. A one-item list arrives
#   unwrapped (the item itself), so lists are normalised in Velocity before #foreach; "$.list.length()"
#   counts right either way. A missing path leaves the variable unset, and "$!{key}" prints nothing.
# * "##" starts a Velocity comment and "#word" can read as a directive, so the markup has no "#" outside
#   directives (colours are rgb()) and no "$" outside "$!{...}" references and directives.
UI_APPROVALS_ACCESS = "/ui/d/approvals/access-request/requested-items"  # ISC Approvals > Access Requests
UI_LAUNCHPAD = "/ui/d/launchpad"
UI_PLUGIN = "/ui/plugin/{id}"
ITEM_TYPE_WORDS = {"ACCESS_PROFILE": "access profile", "ROLE": "role", "ENTITLEMENT": "entitlement"}
DEFAULT_HELP = "Contact your SailPoint administrator."

_FONT = "font-family:Arial,Helvetica,sans-serif"
_TEXT, _MUTED, _RULE, _WHITE = "rgb(31,41,55)", "rgb(107,114,128)", "rgb(229,231,235)", "rgb(255,255,255)"
ACCENTS = {"requested": "rgb(21,128,61)", "rejected": "rgb(180,83,9)"}


@dataclass(frozen=True)
class EmailLinks:
    """Where the emails point. `ui` is the tenant's UI address (config.ui_base_url); `plugin_id` the plugin
    instance, looked up by alias at install time (None while the plugin isn't uploaded). Without `ui` the
    emails still name every page, just without links."""
    ui: str | None = None
    plugin_id: str | None = None

    def url(self, path: str) -> str | None:
        return f"{self.ui}{path}" if self.ui else None

    def plugin(self, cfg: Config) -> str | None:
        return self.url(UI_PLUGIN.format(id=self.plugin_id)) if cfg.deploy_plugin and self.plugin_id else None


def _ref(name: str) -> str:
    """A context variable in the body; prints nothing when it is unset."""
    return "$!{" + name + "}"


def _is(name: str, value: str, html_yes: str, html_no: str = "") -> str:
    """Velocity: `html_yes` when the context variable equals `value` (as text), else `html_no`."""
    return f'#if("{_ref(name)}" == "{value}"){html_yes}' + (f"#{{else}}{html_no}" if html_no else "") + "#{end}"


def _when(name: str, html_yes: str, html_no: str = "") -> str:
    """Velocity: `html_yes` when the context variable is set and not empty (the same in Velocity 1.7 and 2.x)."""
    return f'#if("{_ref(name)}" != ""){html_yes}' + (f"#{{else}}{html_no}" if html_no else "") + "#{end}"


def _link(url: str | None, text: str, accent: str) -> str:
    return f'<a href="{url}" style="color:{accent};font-weight:bold">{text}</a>' if url else f"<b>{text}</b>"


def _rows(rows: list[tuple[str, str]]) -> str:
    cell = "padding:4px 0;vertical-align:top"
    return ('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            'style="border-collapse:collapse">'
            + "".join(f'<tr><td style="{cell};padding-right:12px;width:104px;color:{_MUTED}">{label}</td>'
                      f'<td style="{cell}">{value}</td></tr>' for label, value in rows)
            + "</table>")


def _list(lines: list[str]) -> str:
    return ('<ul style="margin:0;padding-left:20px">'
            + "".join(f'<li style="margin:0 0 6px">{line}</li>' for line in lines) + "</ul>")


def _section(heading: str, content: str) -> str:
    return (f'<tr><td style="padding:14px 18px 2px;font-size:12px;font-weight:bold;letter-spacing:0.5px;'
            f'text-transform:uppercase;color:{_MUTED}">{heading}</td></tr>'
            f'<tr><td style="padding:2px 18px 6px">{content}</td></tr>')


def _items_html() -> str:
    """Each access item on its own line as "name (type)". A one-item list arrives as the item itself."""
    kinds = "".join(f'#elseif("$!{{i.type}}" == "{t}"){w}' for t, w in ITEM_TYPE_WORDS.items())
    kinds = kinds.replace("#elseif", "#if", 1) + "#{else}$!{i.type}#{end}"
    return ("#if($items.name)#set($bulkItems = [$items])#{else}#set($bulkItems = $items)#{end}"
            f'#foreach($i in $bulkItems)<div>$!{{i.name}} <span style="color:{_MUTED}">({kinds})</span></div>#{{end}}')


def _layout(cfg: Config, kind: str, title: str, intro: str, sections: list[tuple[str, str]]) -> str:
    """One email: a coloured title bar, an intro, labelled sections and a footer, as one plain table with
    inline styles (phone and Outlook friendly: no CSS classes, images or external files)."""
    dry = ("" if cfg.live else
           '<tr><td style="padding:8px 18px;background:rgb(254,243,199);color:rgb(120,53,15);font-size:13px">'
           "<b>DRY RUN.</b> This installation is in dry-run mode: the checks and the emails run, but no access "
           "is ever requested.</td></tr>")
    return ('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'style="width:100%;max-width:640px;border-collapse:collapse;{_FONT};font-size:14px;line-height:1.5;'
            f'color:{_TEXT};background:{_WHITE};border:1px solid {_RULE}">'
            f'<tr><td style="padding:14px 18px;background:{ACCENTS[kind]};color:{_WHITE};font-size:18px;'
            f'font-weight:bold">{title}</td></tr>{dry}'
            f'<tr><td style="padding:14px 18px 4px">{intro}</td></tr>'
            + "".join(_section(h, c) for h, c in sections)
            + _section("Need help?", _ref("help"))
            + f'<tr><td style="padding:12px 18px;border-top:1px solid {_RULE};font-size:12px;color:{_MUTED}">'
            f"Sent by {_ref('tool')} in SailPoint. Search SailPoint for the INC number to find every part of this "
            "request.</td></tr></table>")


_LINKABLE = re.compile(r"https?://[^\s<>\"']*[^\s<>\"'.,;:!?)]|[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def help_html(cfg: Config) -> str:
    """`notifications.helpContact` as HTML (escaped; web and email addresses become links), or the default.
    It travels in the email context, so it's never read as a template."""
    text = cfg.help_contact or DEFAULT_HELP
    out, last = [], 0
    for m in _LINKABLE.finditer(text):
        target = m.group(0)
        href = target if target.startswith("http") else f"mailto:{target}"
        out += [html.escape(text[last:m.start()]), f'<a href="{html.escape(href)}">{html.escape(target)}</a>']
        last = m.end()
    return "".join(out) + html.escape(text[last:])


def _email(cfg: Config, subject: str, body: str, context: dict[str, Any]) -> dict[str, Any]:
    attrs: dict[str, Any] = {"subject": subject, "body": body, "context": context}
    if cfg.override_recipients:
        attrs["recipientEmailList"] = list(cfg.override_recipients)   # demo/test tenants: never mail real people
    else:
        attrs["recipientEmailList.$"] = "$.getRequester.attributes.email"
    return {"actionId": "sp:send-email", "type": "action", "versionNumber": 2, "attributes": attrs}


def email_context(cfg: Config) -> dict[str, Any]:
    """What every email shows, read from the run as JSONPath values (never templates, see above), plus config
    text as plain values."""
    p = PATHS
    return {
        "inc.$": p["inc"], "part.$": p["partLabel"], "items.$": p["items"], "people.$": p["people"] + ".length()",
        "access.$": p["accessLabel"], "justification.$": p["justification"],
        "requester.$": "$.getRequester.attributes.displayName",
        "tool": f"the '{cfg.base_name}' workflow", "pluginName": cfg.plugin_display_name,
        "launcherName": cfg.launcher_name, "help": help_html(cfg)}


def _what(*, access: bool = True) -> tuple[str, str]:
    part = _when("part", f" {_ref('part')}: one part of a larger request with the same INC")
    rows = [("INC", f"<b>{_ref('inc')}</b>{part}"), ("Access", _items_html()),
            ("For", _is("people", "1", "1 person", f"{_ref('people')} people"))]
    if access:
        rows.append(("How long", _is("access", "Permanent", "Permanent (no end date)",
                                     f"{_ref('access')} (removed automatically when it ends)")))
    return "What", _rows(rows)


def _why() -> tuple[str, str]:
    return "Why", _rows([("Justification", f'<span style="white-space:pre-wrap">{_ref("justification")}</span>'),
                         ("Requested by", _ref("requester"))])


def _resubmit(cfg: Config, links: EmailLinks, accent: str) -> str:
    """Where to submit again: the plugin's New request tab and/or the Launchpad, whichever is installed."""
    ways = []
    if cfg.deploy_plugin:
        ways.append(_link(links.plugin(cfg), f"'{_ref('pluginName')}'", accent) + " (New request tab)")
    if cfg.deploy_launcher:
        ways.append(_link(links.url(UI_LAUNCHPAD), "Launchpad", accent) + f" → '{_ref('launcherName')}'")
    return " or ".join(ways)


def requested_email(cfg: Config, links: EmailLinks, subject_id: str) -> dict[str, Any]:
    """Sent once the access requests are filed (or, in dry-run mode, once the request passed every check)."""
    accent = ACCENTS["requested"]
    request = f"bulk access request <b>{_ref('inc')}{_ref('part')}</b>"
    if cfg.live:
        intro = (f"Access was requested for every person and item on your {request}. Each item is now approved "
                 "the way it's set up in SailPoint (its owner, the person's manager, …); items without an approval "
                 "of their own are granted right away.")
        approvers = ("Item approvers decide in SailPoint: "
                     + _link(links.url(UI_APPROVALS_ACCESS), "Approvals → Access Requests", accent))
        if cfg.plugin_approvals_enabled:
            approvers += (", or all at once on the Approvals tab of "
                          + _link(links.plugin(cfg), f"'{_ref('pluginName')}'", accent))
        nxt = [approvers + ".",
               "The access requests are filed by the workflow on everyone's behalf, so they don't appear in your "
               "Request Center → My Requests. An administrator finds them all by searching SailPoint for the INC "
               "number.",
               _is("access", "Permanent", "Each person keeps the access until it's removed.",
                   "Temporary access is removed automatically when it ends, counted from when it was requested.")]
        title, subject = "Bulk access requested", f"Requested: bulk access {subject_id}"
    else:
        intro = f"Your {request} passed every check. Nothing was requested, because this installation is in dry-run mode."
        nxt = ["Nothing to do: dry-run mode only tries out the checks and the emails. An administrator switches it "
               "to live (<b>mode: live</b>) when it's ready."]
        title, subject = "Bulk access request checked", f"Checked: bulk access {subject_id} (DRY RUN: nothing was requested)"
    body = _layout(cfg, "requested", title, intro, [_what(), _why(), ("What happens next", _list(nxt))])
    return _email(cfg, subject, body, email_context(cfg))


def rejected_email(cfg: Config, links: EmailLinks, *, title: str, field: str, problem: str,
                   entered: tuple[str, ...] = (), access: bool = False) -> dict[str, Any]:
    """Sent when the workflow stops a request before anything is requested: which field to fix, and where to submit
    again. `entered` are the paths of the refused value (e.g. a duration and its unit), shown back to the requester."""
    accent = ACCENTS["rejected"]
    ctx = {**email_context(cfg), "field": field, "problem": problem}
    rows = [("Field", f"<b>{_ref('field')}</b>"), ("Problem", _ref("problem"))]
    if entered:
        refs = "".join(_ref(f"entered{i}") for i in range(len(entered)))
        ctx.update({f"entered{i}.$": path for i, path in enumerate(entered)})
        rows.append(("You entered", f'#if("{refs}" != ""){refs}#{{else}}<span style="color:{_MUTED}">(nothing)</span>#{{end}}'))
    nxt = [f"Nothing was requested. Fix <b>{_ref('field')}</b> and submit the request again: "
           + _resubmit(cfg, links, accent) + "."]
    body = _layout(cfg, "rejected", f"Fix your bulk access request: {_ref('field')}",
                   f"Your bulk access request <b>{_ref('inc')}{_ref('part')}</b> was stopped before anything was "
                   "requested. Fix the field below and submit it again.",
                   [("What to fix", _rows(rows)), _what(access=access), _why(), ("What to do now", _list(nxt))])
    # No INC in the subject: on these paths it may be the refused value itself.
    return _email(cfg, f"Not requested: {title} ({cfg.base_name})", body, ctx)


def _choice(display: str, comparator: str, a: str, b: Any, yes: str, no: str, *,
            action: str = "sp:compare-strings") -> dict[str, Any]:
    return {"actionId": action, "type": "choice", "displayName": display,
            "choiceList": [{"comparator": comparator, "variableA.$": a, "variableB": b, "nextStep": yes}],
            "defaultStep": no}


def launcher_step_descriptions(cfg: Config) -> dict[str, str]:
    """What each Launcher workflow step checks or does, and why: shown as the step's Description in the builder."""
    units = launcher_workflow_units(cfg)
    unit_words = ", ".join(u.lower() for u in units) or "none"
    stopped = "Nothing is requested."
    d = {
        "Interactive Form": "Shows the bulk request form in the Launchpad to the person who launched it (people, items, "
                            "INC, justification, how long the access lasts) and waits for them to submit it.",
        "Get Requester": "Looks up who submitted the form: their name goes on every access request's comment and they "
                         "receive the emails.",
        "INC Valid?": f"Is the INC number in the right format ({cfg.inc_pattern})? The form checks it too; this repeats "
                      "the check because the form can also be filled in through the API (the plugin does).",
        "Reject Bad INC": f"Tells the requester in the Launchpad that the INC number is invalid. {stopped}",
        "Email Reject Bad INC": "Emails the requester the same message, so they still have it after closing the "
                                "Launchpad.",
        DEFINE_ACCESS: "Starts with permanent access and a single part. The steps below change these when temporary "
                       "access was chosen, or when the plugin sent the request in parts.",
        "Part Given?": "Did the plugin send this as one part of a bigger request (\" (2/3)\")? The Launchpad never does; "
                       "only a well-formed part label is used, so a stray value can't end up in the messages.",
        "Set Part": "Keeps the part label, so the messages and the email say which part this is.",
        "Temporary?": "Was temporary access chosen? If not, the access is permanent.",
        "Duration Valid?": "Is the duration a whole number of 1 or more? An invalid duration would make the access "
                           f"request step fail, so it's stopped here instead. {stopped}",
        "Unit Valid?": f"Was an allowed unit chosen ({unit_words})?",
        "Set Temporary Access": "Builds the removal duration the access request needs (for example 30d) and the label "
                                "shown on the requests and emails (for example Temporary: 30d).",
        "Within Limit?": f"Is the access no longer than the {cfg.temporary_max_days}-day maximum set by the admins?",
        "Reject Too Long": f"Tells the requester the access would last longer than allowed. {stopped}",
        "Email Reject Too Long": "Emails the requester the same message.",
        "Reject Bad Duration": f"Tells the requester the duration isn't a whole number of 1 or more. {stopped}",
        "Email Reject Bad Duration": "Emails the requester the same message.",
        "Reject Bad Unit": f"Tells the requester to choose a unit for the duration. {stopped}",
        "Email Reject Bad Unit": "Emails the requester the same message.",
        "Notify Requested": ("Tells the requester in the Launchpad that the access is being requested. Each item is then "
                             "approved the way it's set up in SailPoint." if cfg.live else
                             "Tells the requester the request passed every check. Dry-run mode: nothing is requested."),
        "Request Access": "Requests every chosen item for each person: one access request per person, in parallel (up "
                          "to 250 people per run). SailPoint allows few people per request and no loop inside a loop, "
                          "hence one loop over the people.",
        "Manage Access": "Files this person's access request: every chosen item, the bulk comment (INC, requester, "
                         "access, justification) and, for temporary access, the removal duration. The workflow's owner "
                         "is recorded as the requester.",
        "End Step - Success Item": "This person's request is filed.",
        "Email Requested": ("Emails the requester what was requested, for whom, and where to follow it." if cfg.live else
                            "Emails the requester that the request was checked. Dry-run mode: nothing is requested."),
        "End Step - Success": ("The access requests are filed; each now waits for its own approval, if it has one."
                               if cfg.live else "The request passed every check (dry run)."),
    }
    return d


def _describe(steps: dict[str, Any], descriptions: dict[str, str]) -> None:
    """Gives every step (loop bodies too) its description, keeping a failure step's own."""
    for name, step in steps.items():
        if step.get("type") != "failure" and name in descriptions:
            step["description"] = descriptions[name]
        if step.get("actionId") == "sp:loop:iterator":
            _describe(step["attributes"]["steps"], descriptions)


def bulk_workflow(cfg: Config, *, owner_id: str, owner_name: str | None = None, form_id: str,
                  workflow_id: str | None = None, links: EmailLinks | None = None) -> dict[str, Any]:
    """The Launcher's workflow. `workflow_id` (known after creation) scopes the trigger; `links` (the tenant's UI
    address and the plugin instance, looked up at install time) are used in the emails."""
    if not form_id:
        raise ValueError("form_id is required")
    p = PATHS
    links = links or EmailLinks()
    inc = _t(p["inc"])
    part = _t(p["partLabel"])     # "" or " (2/3)"; only the plugin sends it
    label = _t(p["accessLabel"])
    live = cfg.live
    temporary_units = launcher_workflow_units(cfg)

    steps: dict[str, Any] = {}
    steps["Interactive Form"] = {
        "actionId": "sp:interactive-form", "type": "action", "versionNumber": 1, "displayName": "Bulk request form",
        "attributes": {"formDefinitionId": form_id, "interactiveProcessId.$": "$.trigger.interactiveProcessId",
                       "ownerId.$": "$.trigger.launchedBy.id", "title": cfg.base_name,
                       "message": "<p>Request access for several people at once. Each item is approved the way it's "
                                  "set up in SailPoint, and the ServiceNow INC number is recorded on every request.</p>"},
        "nextStep": "Get Requester"}
    steps["Get Requester"] = {"actionId": "sp:get-identity", "type": "action", "versionNumber": 2,
                              "displayName": "Requester", "attributes": {"id.$": p["requester"]}, "nextStep": "INC Valid?"}
    # Defence in depth: the form (and the plugin) already enforce these.
    steps["INC Valid?"] = _choice("INC number valid?", "StringMatches", p["inc"], cfg.inc_pattern, DEFINE_ACCESS,
                                  "Reject Bad INC")

    def stop(name: str, title: str, field: str, message: str, *, entered: tuple[str, ...] = ()) -> None:
        """Stop before requesting anything: show `title` and `message` in the Launchpad (the plugin reads them back
        when it submitted through the Launcher), then email them."""
        steps[name] = {"actionId": "sp:interactive-message", "type": "action", "versionNumber": 1, "displayName": title,
                       "attributes": {"category": "ERROR", "interactiveProcessId.$": "$.trigger.interactiveProcessId",
                                      "ownerId.$": "$.trigger.launchedBy.id", "title": title,
                                      "message": f"<p>{field}: {message}</p><p>Nothing was requested. "
                                                 "Fix it and submit again; you'll also get this by email.</p>"},
                       "nextStep": f"Email {name}"}
        steps[f"Email {name}"] = {**rejected_email(cfg, links, title=title, field=field, problem=message,
                                                   entered=entered),
                                  "displayName": f"Email: {title}", "nextStep": "End Step - Rejected"}

    stop("Reject Bad INC", "Invalid INC number", "ServiceNow incident (INC) number", cfg.inc_message, entered=(p["inc"],))

    # The access choice: permanent ("" / "Permanent") unless the form asks for temporary access;
    # and the part label: "" (one part) unless the plugin sent " (k/n)".
    after_part = "Temporary?" if temporary_units else "Notify Requested"
    steps[DEFINE_ACCESS] = {
        "attributes": {"id": "sp:define-variable", "variables": [
            {"name": "removeDuration", "description": "Manage Access removeDuration; empty means permanent",
             "variableA": "permanent",
             "transforms": [{"id": "sp:transform:replace:string", "input": {"pattern": "permanent", "replacement": ""}}]},
            {"name": "accessLabel", "description": "Shown on the access requests and the emails",
             "variableA": "Permanent", "transforms": []},
            {"name": "partLabel", "description": "\" (2/3)\" when the plugin sent the request in parts; empty otherwise",
             "variableA": "single",
             "transforms": [{"id": "sp:transform:replace:string", "input": {"pattern": "single", "replacement": ""}}]}]},
        "type": "Mutation", "displayName": "Access: permanent unless temporary was chosen; one part",
        "nextStep": "Part Given?"}
    # The Launchpad never sends it; only a well-formed " (k/n)" is used.
    steps["Part Given?"] = _choice("Sent in parts?", "StringMatches", p["partLabelInput"], PART_LABEL_REGEX,
                                   "Set Part", after_part)
    steps["Set Part"] = {
        "attributes": {"id": "sp:update-variable", "variables": [
            {"name": f"{ACCESS_VARS}.partLabel", "description": "", "variableA.$": p["partLabelInput"],
             "transforms": []}]},
        "type": "Mutation", "displayName": "Part of a bigger request", "nextStep": after_part}
    if temporary_units:
        suffixes = "|".join(DURATION_UNITS[u] for u in temporary_units)
        steps["Temporary?"] = _choice("Temporary access?", "BooleanEquals", p["accessType"], True,
                                      "Duration Valid?", "Notify Requested", action="sp:compare-boolean")
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
            "nextStep": "Within Limit?" if cfg.temporary_max_days else "Notify Requested"}
        if cfg.temporary_max_days:
            steps["Within Limit?"] = _choice(f"At most {cfg.temporary_max_days} days?", "StringMatches",
                                             p["removeDuration"], duration_regex(temporary_units, cfg.temporary_max_days),
                                             "Notify Requested", "Reject Too Long")
            stop("Reject Too Long", "Duration too long", "Duration", msg_max_days(cfg.temporary_max_days),
                 entered=(p["duration"], p["durationUnit"]))
        stop("Reject Bad Duration", "Invalid duration", "Duration", MSG_DURATION_NUMBER, entered=(p["duration"],))
        stop("Reject Bad Unit", "Choose a unit", "Unit", MSG_DURATION_UNIT, entered=(p["durationUnit"],))

    effect = ("is being requested for everyone on it. Each item is approved the way it's set up in SailPoint; items "
              "without an approval of their own are granted right away" if live
              else "passed every check. Nothing was requested (DRY RUN)")
    steps["Notify Requested"] = {
        "actionId": "sp:interactive-message", "type": "action", "versionNumber": 1, "displayName": "Submitted",
        "attributes": {"category": "INFO", "interactiveProcessId.$": "$.trigger.interactiveProcessId",
                       "ownerId.$": "$.trigger.launchedBy.id",
                       "title": f"Requested: {inc}{part}" if live else f"Checked: {inc}{part}",
                       "message": f"<p>Your bulk request <b>{inc}</b>{part} ({label}) {effect}. "
                                  "You'll get an email with the details.</p>"},
        "nextStep": "Request Access" if live else "Email Requested"}

    if live:
        # One loop over the people; each iteration requests every chosen item for one
        # person. (No nested loops in SailPoint workflows, and max 10 recipients per request.)
        # Steps inside a loop only see $.loop.*, so the whole workflow state is passed in as
        # the loop context and the items / comment parts are read from $.loop.context.
        def in_loop(path: str) -> str:
            return "$.loop.context" + path[1:]
        # rules.bulk_comment / parse_bulk_comment (and rules.ts) write and read the same format; keep them in step.
        loop_comment = COMMENT_SEPARATOR.join([
            _t(in_loop(p["inc"])),
            f"{REQUESTER_LABEL} {_t(in_loop('$.getRequester.attributes.displayName'))}",
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
            "nextStep": "Email Requested"}

    steps["Email Requested"] = {**requested_email(cfg, links, f"{inc}{part}"),
                                "displayName": "Email: requested" if live else "Email: checked (dry run)",
                                "nextStep": "End Step - Success"}
    steps["End Step - Success"] = _success()
    steps["End Step - Rejected"] = _failure()
    _describe(steps, launcher_step_descriptions(cfg))

    trigger: dict[str, Any] = {"type": "EVENT", "attributes": {"id": "idn:interactive-process-launched"}}
    if workflow_id:
        # The event fires for every launcher in the tenant; only react to our own.
        trigger["attributes"]["filter.$"] = f"$[?(@.workflowId == '{workflow_id}')]"
    return {"name": cfg.launcher_workflow_name,
            "description": (f"{cfg.prefix} Bulk Access Request ({cfg.mode}). Started by the '{cfg.launcher_name}' "
                            "Launcher. Installed by bulk-access-request/launcher/install.py."),
            "owner": _owner(owner_id, owner_name), "enabled": False,
            "definition": {"start": "Interactive Form", "steps": steps}, "trigger": trigger}


# ───────────────────────────────────────────────────────────── launcher ──
def bulk_launcher(cfg: Config, workflow_id: str) -> dict[str, Any]:
    return {
        "name": cfg.launcher_name,
        "description": "Request access for several people at once from the Request Center catalog. Each item is "
                       "approved the way it's set up in SailPoint; a ServiceNow INC number is required and recorded "
                       "on every request.",
        "type": "INTERACTIVE_PROCESS",
        "disabled": False,
        "reference": {"type": "WORKFLOW", "id": workflow_id},
        "config": "{}",
    }


def launcher_access_profile(cfg: Config, owner_id: str, entitlement: dict[str, Any]) -> dict[str, Any]:
    """Who may make bulk requests. SailPoint creates an `assignedLaunchers` entitlement on the
    built-in IdentityNow source for every Launcher; only identities holding it see it in the
    Launchpad. Wrapping it in a requestable access profile lets users ask for it in the
    Request Center (or admins grant it) like any other access. The plugin checks it too."""
    source = entitlement.get("source") or {}
    return {
        "name": cfg.launcher_access_profile_name,
        "description": f"Lets the holder make bulk access requests: the '{cfg.launcher_name}' Launcher in the Launchpad"
                       + (f" and the '{cfg.plugin_display_name}' page" if cfg.deploy_plugin else "")
                       + " (a ServiceNow INC number on every request).",
        "owner": _owner(owner_id),
        "source": {"id": source.get("id"), "type": "SOURCE", "name": source.get("name")},
        "entitlements": [{"id": entitlement["id"], "type": "ENTITLEMENT", "name": entitlement.get("name")}],
        "enabled": True,
        "requestable": True,
        "accessRequestConfig": {"commentsRequired": False, "denialCommentsRequired": False,
                                "approvalSchemes": [{"approverType": "MANAGER"}] if cfg.launcher_access_approval == "MANAGER" else []},
    }


def access_profile_changes(current: dict[str, Any], wanted: dict[str, Any]) -> list[dict[str, Any]]:
    """JSON Patch ops that bring an installed Launcher Access profile in line with `wanted`
    (launcher_access_profile): its description, entitlements and approval setup, and only what differs.

    An unchanged profile gets no PATCH: every update re-indexes it, and until requestable-objects catches up it
    can report holders as AVAILABLE (seen live), which turns them away from the plugin."""
    def ids(profile: dict[str, Any]) -> list[str]:
        return sorted(e.get("id") for e in profile.get("entitlements") or [])

    def approval(profile: dict[str, Any]) -> tuple:
        cfg = profile.get("accessRequestConfig") or {}
        return (bool(cfg.get("commentsRequired")), bool(cfg.get("denialCommentsRequired")),
                [s.get("approverType") for s in cfg.get("approvalSchemes") or []])

    ops = []
    if (current.get("description") or "") != wanted["description"]:
        ops.append({"op": "replace", "path": "/description", "value": wanted["description"]})
    if ids(current) != ids(wanted):
        ops.append({"op": "replace", "path": "/entitlements", "value": wanted["entitlements"]})
    if approval(current) != approval(wanted):
        ops.append({"op": "replace", "path": "/accessRequestConfig", "value": wanted["accessRequestConfig"]})
    return ops


def pretty(obj: Any) -> str:
    return json.dumps(obj, indent=2, sort_keys=False)


__all__ = ["access_profile_changes", "bulk_form", "bulk_workflow", "bulk_launcher", "EmailLinks", "launcher_duration_units",
           "launcher_offers_temporary", "launcher_workflow_units", "F_PART_LABEL", "PART_LABEL_REGEX"]
