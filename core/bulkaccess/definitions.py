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
  passes the same fields as trigger input. Must stay DISABLED. Only installed when
  `plugin.submit` is "test-endpoint": by default the plugin submits through the
  Launcher instead (it launches it and fills in the form as the signed-in user,
  setting the hidden `partLabel` field when a request is sent in parts).

Everything below was confirmed against a live tenant (see INSTALL.md
"How it works"): REGEX validation shape, STATIC options carrying full access
objects, Generic Approval encoding, and Manage Access inside one loop over the
people (SailPoint rejects nested loops and caps a request at 10 recipients, so
one request per person -- each with all the chosen items -- has no limits).
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
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
# Launcher form: hidden, set only by the plugin (launcher submit mode) when a request goes out in parts.
F_PART_LABEL = "partLabel"
# What the Launcher workflow accepts as a part label (rules.part_label: " (2/3)"); anything else means one part.
PART_LABEL_REGEX = r"^ \([1-9][0-9]*/[1-9][0-9]*\)$"

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
        # Hidden: the Launchpad never shows it and leaves it empty. The plugin (launcher submit mode) sets
        # it to " (2/3)" when a request goes out in parts; the workflow checks it (PART_LABEL_REGEX).
        {"id": "partLabel", "key": F_PART_LABEL, "elementType": "HIDDEN", "validations": [],
         "config": {"label": "Part (set by the plugin)", "default": ""}},
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
                "durationUnit": f"{base}.{F_DURATION_UNIT}", "partLabelInput": f"{base}.{F_PART_LABEL}",
                "partLabel": f"{ACCESS_VARS}.partLabel",
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
UI_APPROVALS_OTHER = "/ui/d/approvals/other/requested-items"            # ISC Approvals > Other (generic approvals)
UI_APPROVALS_ACCESS = "/ui/d/approvals/access-request/requested-items"  # ISC Approvals > Access Requests
UI_LAUNCHPAD = "/ui/d/launchpad"
UI_PLUGIN = "/ui/plugin/{id}"
ITEM_TYPE_WORDS = {"ACCESS_PROFILE": "access profile", "ROLE": "role", "ENTITLEMENT": "entitlement"}
DEFAULT_HELP = "Contact your SailPoint administrator."
EMAIL_STEPS = ("Email Pending", "Email Approved", "Email Denied")

_FONT = "font-family:Arial,Helvetica,sans-serif"
_TEXT, _MUTED, _RULE, _WHITE = "rgb(31,41,55)", "rgb(107,114,128)", "rgb(229,231,235)", "rgb(255,255,255)"
ACCENTS = {"pending": "rgb(29,78,216)", "approved": "rgb(21,128,61)", "denied": "rgb(185,28,28)",
           "rejected": "rgb(180,83,9)"}


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


def _comments_html() -> str:
    """The decision's comment: the approval's comments after the first (the first is the request's own
    "INC: justification"). A one-item list arrives as the comment itself."""
    return ("#if($comments.comment)#set($bulkComments = [$comments])#{else}#set($bulkComments = $comments)#{end}"
            '#set($bulkFirst = "yes")#set($bulkSaid = "")'
            '#foreach($c in $bulkComments)#if("$!{bulkFirst}" == "yes")#set($bulkFirst = "")'
            '#{else}<div style="white-space:pre-wrap">$!{c.comment}</div>#set($bulkSaid = "yes")#{end}#{end}'
            f'#if("$!{{bulkSaid}}" == "")<span style="color:{_MUTED}">No comment</span>#{{end}}')


def _layout(cfg: Config, kind: str, title: str, intro: str, sections: list[tuple[str, str]]) -> str:
    """One email: a coloured title bar, an intro, labelled sections and a footer, as one plain table with
    inline styles (phone and Outlook friendly: no CSS classes, images or external files)."""
    dry = ("" if cfg.live else
           '<tr><td style="padding:8px 18px;background:rgb(254,243,199);color:rgb(120,53,15);font-size:13px">'
           "<b>DRY RUN.</b> This installation is in dry-run mode: the approval and the emails run, but no access "
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


def _email(cfg: Config, subject: str, body: str, context: dict[str, Any], *, cc_approver: bool) -> dict[str, Any]:
    attrs: dict[str, Any] = {"subject": subject, "body": body, "context": context}
    if cfg.override_recipients:
        attrs["recipientEmailList"] = list(cfg.override_recipients)   # demo/test tenants: never mail real people
    else:
        attrs["recipientEmailList.$"] = "$.getRequester.attributes.email"
        if cc_approver and cfg.cc_approver:
            attrs["carbonCopy.$"] = "$.getApprover.attributes.email"
    return {"actionId": "sp:send-email", "type": "action", "versionNumber": 2, "attributes": attrs}


def email_context(cfg: Config, p: dict[str, str], *, decision: bool = False) -> dict[str, Any]:
    """What every email shows, read from the run (`p`: the variant's paths) as JSONPath values (never
    templates, see above), plus config text as plain values."""
    ctx: dict[str, Any] = {
        "inc.$": p["inc"], "part.$": p["partLabel"], "items.$": p["items"], "people.$": p["people"] + ".length()",
        "access.$": p["accessLabel"], "justification.$": p["justification"],
        "requester.$": "$.getRequester.attributes.displayName", "approver.$": "$.getApprover.attributes.displayName",
        "tool": f"the '{cfg.base_name}' workflow", "pluginName": cfg.plugin_display_name,
        "launcherName": cfg.launcher_name, "help": help_html(cfg)}
    if decision:
        ctx.update({"status.$": "$.bulkApproval.status", "comments.$": "$.bulkApproval.comments",
                    "approvedBy.$": "$.bulkApproval.approvedBy[0].name",
                    "rejectedBy.$": "$.bulkApproval.rejectedBy[0].name"})
    return ctx


def _decider(fallback: str) -> str:
    """Who decided: the approver, or an admin acting on their behalf (approvedBy / rejectedBy)."""
    return (f'#if("$!{{approvedBy}}" != ""){_ref("approvedBy")}#elseif("$!{{rejectedBy}}" != ""){_ref("rejectedBy")}'
            f"#{{else}}{fallback}#{{end}}")


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


def _decision() -> tuple[str, str]:
    by = _is("status", "EXPIRED", "Nobody (it expired)", _decider(f'<span style="color:{_MUTED}">(not recorded)</span>'))
    return "Decision", _rows([("Approver", _ref("approver")), ("Decided by", by), ("Comment", _comments_html())])


def _resubmit(cfg: Config, links: EmailLinks, accent: str) -> str:
    """Where to submit again: the plugin's New request tab and/or the Launchpad, whichever is installed."""
    ways = []
    if cfg.deploy_plugin:
        ways.append(_link(links.plugin(cfg), f"'{_ref('pluginName')}'", accent) + " (New request tab)")
    if cfg.deploy_launcher:
        ways.append(_link(links.url(UI_LAUNCHPAD), "Launchpad", accent) + f" → '{_ref('launcherName')}'")
    return " or ".join(ways)


def _track(cfg: Config, links: EmailLinks, accent: str) -> str:
    if not cfg.deploy_plugin:
        return ""
    return "Track it in " + _link(links.plugin(cfg), f"'{_ref('pluginName')}' → My bulk requests", accent) + "."


def pending_email(cfg: Config, p: dict[str, str], links: EmailLinks, subject_id: str) -> dict[str, Any]:
    """Sent once the request waits for the bulk approver: what was asked, and where the approver decides."""
    accent = ACCENTS["pending"]
    approver = _ref("approver")
    at_timeout = ("it is approved automatically" if cfg.approval_action_at_timeout == "APPROVED"
                  else "it expires and nothing is requested")
    effect = ("every person listed is requested every item" if cfg.live
              else "in this dry-run installation nothing is requested")
    nxt = [f"{approver} decides in SailPoint: " + _link(links.url(UI_APPROVALS_OTHER), "Approvals → Other", accent)
           + f", task <b>Grant: Bulk access {_ref('inc')}{_ref('part')}</b>. Approving there takes effect at once: "
           f"{effect}.",
           f"If nobody decides within {cfg.approval_timeout_days} days, {at_timeout}.",
           _track(cfg, links, accent), "You'll get another email when it's decided."]
    body = _layout(cfg, "pending", "Waiting for approval",
                   f"Your bulk access request <b>{_ref('inc')}{_ref('part')}</b> was sent to <b>{approver}</b> for "
                   "approval. Nothing is requested until it's approved.",
                   [_what(), _why(), ("What happens next", _list([x for x in nxt if x]))])
    return _email(cfg, f"Waiting for approval: bulk access {subject_id}{'' if cfg.live else ' (DRY RUN)'}", body,
                  email_context(cfg, p), cc_approver=True)


def approved_email(cfg: Config, p: dict[str, str], links: EmailLinks, subject_id: str) -> dict[str, Any]:
    accent = ACCENTS["approved"]
    intro = (f"<b>{_decider(_ref('approver'))}</b> approved your bulk access request "
             f"<b>{_ref('inc')}{_ref('part')}</b>. ")
    if cfg.live:
        intro += "Access was requested for every person and item on it."
        item_approvers = ("Items with their own approval (the item's owner, the person's manager, …) still need it "
                          "for each person. Those approvers decide in SailPoint: "
                          + _link(links.url(UI_APPROVALS_ACCESS), "Approvals → Access Requests", accent))
        if cfg.plugin_approvals_enabled:
            item_approvers += (", or all at once on the Approvals tab of "
                               + _link(links.plugin(cfg), f"'{_ref('pluginName')}'", accent))
        nxt = [item_approvers + ".",
               "The access requests are filed by the workflow on everyone's behalf, so they don't appear in your "
               "Request Center → My Requests. " + _track(cfg, links, accent),
               _is("access", "Permanent", "Each person keeps the access until it's removed.",
                   "Temporary access is removed automatically when it ends, counted from when it was requested.")]
    else:
        intro += "Nothing was requested, because this installation is in dry-run mode."
        nxt = ["Nothing to do: dry-run mode only tries out the approval and the emails. An administrator switches it "
               "to live (<b>mode: live</b>) when it's ready.", _track(cfg, links, accent)]
    body = _layout(cfg, "approved", "Bulk access request approved", intro,
                   [_what(), _why(), _decision(), ("What happens next", _list([x.strip() for x in nxt if x]))])
    return _email(cfg, f"Approved: bulk access {subject_id}{'' if cfg.live else ' (DRY RUN: nothing was requested)'}",
                  body, email_context(cfg, p, decision=True), cc_approver=True)


def denied_email(cfg: Config, p: dict[str, str], links: EmailLinks, subject_id: str) -> dict[str, Any]:
    """Sent when the bulk approval ends any other way than APPROVED: denied (REJECTED) or EXPIRED."""
    accent = ACCENTS["denied"]
    request = f"bulk access request <b>{_ref('inc')}{_ref('part')}</b>"
    title = _is("status", "EXPIRED", "Bulk access request expired", "Bulk access request not approved")
    intro = _is("status", "EXPIRED",
                f"Nobody decided your {request} within {cfg.approval_timeout_days} days, so it expired. "
                "Nothing was requested.",
                f"<b>{_decider(_ref('approver'))}</b> did not approve your {request} "
                f"(status: {_ref('status')}). Nothing was requested.")
    nxt = [_is("status", "EXPIRED",
               f"Check with {_ref('approver')} or choose another approver, then submit it again: ",
               "Read the comment above, fix what it asks for (people, items, how long or the justification) and "
               "submit a new request (the same INC is fine): ") + _resubmit(cfg, links, accent) + ".",
           f"Questions about the decision? Ask {_ref('approver')}."]
    body = _layout(cfg, "denied", title, intro, [_what(), _why(), _decision(), ("What to do now", _list(nxt))])
    return _email(cfg, f"Not approved: bulk access {subject_id}", body, email_context(cfg, p, decision=True),
                  cc_approver=True)


def rejected_email(cfg: Config, p: dict[str, str], links: EmailLinks, *, title: str, field: str, problem: str,
                   entered: tuple[str, ...] = (), access: bool = False) -> dict[str, Any]:
    """Sent when the workflow stops a request before any approval: which field to fix, and where to submit
    again. `entered` are the paths of the refused value (e.g. a duration and its unit), shown back to the requester."""
    accent = ACCENTS["rejected"]
    ctx = {**email_context(cfg, p), "field": field, "problem": problem}
    rows = [("Field", f"<b>{_ref('field')}</b>"), ("Problem", _ref("problem"))]
    if entered:
        refs = "".join(_ref(f"entered{i}") for i in range(len(entered)))
        ctx.update({f"entered{i}.$": path for i, path in enumerate(entered)})
        rows.append(("You entered", f'#if("{refs}" != ""){refs}#{{else}}<span style="color:{_MUTED}">(nothing)</span>#{{end}}'))
    nxt = [f"Nothing was sent for approval and nothing was requested. Fix <b>{_ref('field')}</b> and submit the "
           "request again: " + _resubmit(cfg, links, accent) + "."]
    body = _layout(cfg, "rejected", f"Fix your bulk access request: {_ref('field')}",
                   f"Your bulk access request <b>{_ref('inc')}{_ref('part')}</b> was stopped before approval. "
                   "Fix the field below and submit it again.",
                   [("What to fix", _rows(rows)), _what(access=access), _why(), ("What to do now", _list(nxt))])
    # No INC in the subject: on these paths it may be the refused value itself.
    return _email(cfg, f"Not sent for approval: {title} ({cfg.base_name})", body, ctx, cc_approver=False)


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
                  form_id: str | None = None, workflow_id: str | None = None,
                  links: EmailLinks | None = None) -> dict[str, Any]:
    """The bulk-request workflow. `workflow_id` (known after creation) scopes the launcher trigger; `links`
    (the tenant's UI address and the plugin instance, looked up at install time) are used in the emails."""
    p = _paths(variant)
    links = links or EmailLinks()
    inc, who, appr = _t(p["inc"]), _t("$.getRequester.attributes.displayName"), _t("$.getApprover.attributes.displayName")
    part = _t(p["partLabel"])     # "" or " (2/3)"; on the Launcher only the plugin sets it (hidden field)
    label = _t(p["accessLabel"])
    live = cfg.live
    mode_note = "" if live else " (DRY RUN: nothing was requested)"
    approval_start = "Email Pending" if cfg.pending_email else "Bulk Approval"
    temporary_units = launcher_workflow_units(cfg) if variant == "launcher" else ()

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

    def stop(name: str, title: str, field: str, message: str, *, entered: tuple[str, ...] = (),
             access: bool = False) -> None:
        """Stop before the approval. The Launcher shows `title` and `message` in the Launchpad (the plugin reads
        them back when it submitted through the Launcher) and then emails; the plugin workflow emails."""
        email = {**rejected_email(cfg, p, links, title=title, field=field, problem=message, entered=entered,
                                  access=access),
                 "displayName": f"Email: {title}", "nextStep": "End Step - Rejected"}
        if variant == "launcher":
            steps[name] = {"actionId": "sp:interactive-message", "type": "action", "versionNumber": 1, "displayName": title,
                           "attributes": {"category": "ERROR", "interactiveProcessId.$": "$.trigger.interactiveProcessId",
                                          "ownerId.$": "$.trigger.launchedBy.id", "title": title,
                                          "message": f"<p>{field}: {message}</p><p>Nothing was sent for approval. "
                                                     "Fix it and submit again; you'll also get this by email.</p>"},
                           "nextStep": f"Email {name}"}
            steps[f"Email {name}"] = email
        else:
            steps[name] = email

    stop("Reject Self Approval", "Choose a different approver", "Approver",
         "You can't approve your own bulk request. Choose someone else as the approver.")
    stop("Reject Approver In People", "Choose a different approver", "Approver",
         f"{MSG_APPROVER_IN_PEOPLE} Choose an approver who isn't on the list, or take them off it.")
    stop("Reject Bad INC", "Invalid INC number", "ServiceNow incident (INC) number", cfg.inc_message, entered=(p["inc"],))

    if variant == "launcher":
        # The access choice: permanent ("" / "Permanent") unless the form asks for temporary access;
        # and the part label: "" (one part) unless the plugin set the hidden field to " (k/n)".
        after_part = "Temporary?" if temporary_units else "Notify Pending"
        steps[DEFINE_ACCESS] = {
            "attributes": {"id": "sp:define-variable", "variables": [
                {"name": "removeDuration", "description": "Manage Access removeDuration; empty means permanent",
                 "variableA": "permanent",
                 "transforms": [{"id": "sp:transform:replace:string", "input": {"pattern": "permanent", "replacement": ""}}]},
                {"name": "accessLabel", "description": "Shown on the approval, the access requests and the emails",
                 "variableA": "Permanent", "transforms": []},
                {"name": "partLabel", "description": "\" (2/3)\" when the plugin sent the request in parts; empty otherwise",
                 "variableA": "single",
                 "transforms": [{"id": "sp:transform:replace:string", "input": {"pattern": "single", "replacement": ""}}]}]},
            "type": "Mutation", "displayName": "Access: permanent unless temporary was chosen; one part",
            "nextStep": "Part Given?"}
        # The Launchpad leaves the hidden field empty (or out); only a well-formed " (k/n)" is used.
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
                stop("Reject Too Long", "Duration too long", "Duration", msg_max_days(cfg.temporary_max_days),
                     entered=(p["duration"], p["durationUnit"]))
            stop("Reject Bad Duration", "Invalid duration", "Duration", MSG_DURATION_NUMBER, entered=(p["duration"],))
            stop("Reject Bad Unit", "Choose a unit", "Unit", MSG_DURATION_UNIT, entered=(p["durationUnit"],))
    else:
        # The plugin validates the choice; an invalid removeDuration would only fail after approval.
        steps["Access Valid?"] = _choice("Temporary access valid?", "StringMatches", p["removeDuration"],
                                         plugin_duration_regex(cfg), approval_start, "Reject Bad Duration")
        stop("Reject Bad Duration", "Invalid temporary access", "Temporary access (how long)",
             "The temporary access duration was not valid.", entered=(p["removeDuration"],))

    if variant == "launcher":
        steps["Notify Pending"] = {
            "actionId": "sp:interactive-message", "type": "action", "versionNumber": 1, "displayName": "Submitted",
            "attributes": {"category": "INFO", "interactiveProcessId.$": "$.trigger.interactiveProcessId",
                           "ownerId.$": "$.trigger.launchedBy.id", "title": f"Sent to {appr} for approval",
                           "message": f"<p>Your bulk request <b>{inc}</b>{part} ({label}) is waiting for {appr}, "
                                      "who decides it in SailPoint under Approvals → Other. "
                                      f"You'll get an email when it's decided{mode_note}.</p>"},
            "nextStep": approval_start}
    if cfg.pending_email:
        steps["Email Pending"] = {**pending_email(cfg, p, links, f"{inc}{part}"), "displayName": "Email: waiting for approval",
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

    steps["Email Approved"] = {**approved_email(cfg, p, links, f"{inc}{part}"), "displayName": "Email: approved",
                               "nextStep": "End Step - Success"}
    steps["Email Denied"] = {**denied_email(cfg, p, links, f"{inc}{part}"), "displayName": "Email: not approved",
                             "nextStep": "End Step - Success"}
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
        "name": cfg.launcher_access_profile_name,
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


__all__ = ["bulk_form", "bulk_workflow", "bulk_launcher", "VARIANTS", "APPROVAL_COMMENT_MAX", "PLUGIN_INPUT", "EmailLinks",
           "launcher_duration_units", "launcher_offers_temporary", "launcher_workflow_units", "plugin_duration_regex",
           "F_PART_LABEL", "PART_LABEL_REGEX"]
