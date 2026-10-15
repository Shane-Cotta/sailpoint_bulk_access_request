# Contracts: no bulk approver, no people limit, temporary access, one central config

This is the shared spec for work split across agents (core/Launcher, UI plugin, docs). **Build against this file, not
against another agent's code.** If something here turns out to be wrong or impossible, stop and report it to the
orchestrator instead of changing it alone.

There is **no approval of a bulk request as a whole**. Every access request a bulk request files goes through its item's
own approval, as SailPoint has it set up (owner, manager, source owner, governance group, none). The INC number and the
justification travel on every request's comment (§4).

## Verified live
2026-10-08 (test tenant, spikes), 2026-10-13 (demo tenant, Phase 0 of the approver removal, as the non-admin
`bulk.requester.test` with Launcher Access, and with the admin PAT) and 2026-10-14 (demo tenant, the Submit Access role,
same users):

| Fact | Result |
|---|---|
| `sp:loop:iterator` (Loop) size limit | **250 items**, hard: above that the step fails with "Input has N iterations which exceed 250 iteration limit". 250 ran in about 13 s, in parallel. |
| `sp:serial:iterator` (Serial Loop) | **Not usable.** It silently stops after **50** items with no error (and stops at the first failing item). Don't use it. |
| Manage Access **versionNumber 2** | Has `removeDuration` (hours, days, weeks, months) and `startDate`. v1 had days and weeks only, and "not supported for entitlements". **Use v2.** |
| `removeDuration` encoding | A string `"<n><suffix>"`: `"2h"` → removeDate +2 h, `"1d"` → +1 day, `"1w"` → +7 days, `"1M"` → +1 month. Missing or `""` → permanent. `"abc"` **fails the step** ("timeext: invalid duration"). |
| End date on the Launcher | No workflow transform turns a date into a duration, so **the Launcher offers duration only**. The plugin offers duration **and** end date. |
| `POST /access-requests/v1` as a non-admin | 202 for other people when the tenant allows it (`requestOnBehalfOfConfig.allowRequestOnBehalfOfAnyoneByAnyone`). At most **10** `requestedFor`; items of all three types; each item carries its own `comment` and `removeDate` (an entitlement too). Response: `newRequests[]` with one entry per person and item (`{requestedFor, requestedItemsDetails[{id,type}], accessRequestIds[]}`), plus `existingRequests[]` for ones already open. A 4000-character comment is accepted. A person with a pending request for the item is still listed in `newRequests`, but SailPoint cancels that request at once (`CANCELLED`, `cancelledRequestDetails.comment` "Already has a pending request for this item"). |
| `GET /access-request-status/v1?requested-by=<me>` | The caller's own requests, newest first with `sorters=-created`. Rows: `requestedFor {id,name,type}`, `name` (the item), `state`, `removeDate`, `requesterComment.comment`, `accessRequestPhases[]` and `approvalDetails[]` (`{status, currentOwner{id,name,email}, originalOwner, …}`; empty until the approval phase starts). No `requestedObject`. |
| Items with no approval scheme | An access profile or role with `approvalSchemes: []` goes straight from the approval phase to provisioning (no approval). An entitlement with none of its own falls back to the tenant default `entitlementRequestConfig.grantRequestApprovalSchemes` (e.g. `sourceOwner`). |
| Requester is the item's approver | The approval is reassigned to an admin, not auto-approved (same for the Launcher workflow owner: "…because the Identity X is the Requester"). |
| Cancelling a request | `POST /access-requests/v1/cancel` `{accountActivityId, comment}`, where `accountActivityId` is the status row's `accessRequestId`; works for the requester while it is still open. |
| Who may submit | `GET /requestable-objects/v1?identity-id=<me>&types=ROLE&filters=id eq "<Submit Access role>"` → `requestStatus: ASSIGNED` once granted, `AVAILABLE` without it (non-admin ✔, 2026-10-14); the admin PAT sees the same about 20 s after provisioning. The same call with `types=ACCESS_PROFILE` works for an access profile (the earlier gate, ✔ 2026-10-13). A non-admin can't GET an access profile itself (403). Replacing a profile's `/entitlements` makes it read `AVAILABLE` for holders until their identity is refreshed ✔, so the installers only PATCH fields that differ. |
| An empty requestable role | `POST /roles/v1` with `accessProfiles: []`, `entitlements: []` and no approval schemes is accepted; requested with `POST /access-requests/v1` (type `ROLE`) it is provisioned in about 2 s (`REQUEST_COMPLETED`). `DELETE /roles/v1/{id}` succeeds even while the role is assigned. |
| Holders of an access profile or role | `POST /search/v1` on `identities` with the query `@access(id:"<id>")` lists them. |
| GA `/v1` paths | Every path in `core/bulkaccess/api.py` and `plugin/src/app/bulk/api-paths.ts` answers without `X-SailPoint-Experimental`, with the same filters and shapes as the old `v3`/`v2025` ones (accounts filters and search results compared). `/workflow-executions/v1` has no list (404); `/{id}/history-v2` returns `{definition, trigger, history}`. |

## 1. The central config (`config/<tenant>.json`)
`core/bulkaccess/config.py` is the **only** loader. Schema: `config/bulk-access.example.json`.

| `Config` attribute (Python) | From | Meaning |
|---|---|---|
| `people_max: int \| None` | `people.max` | `None` = no limit (default) |
| `part_size: int` | `people.partSize` | 1..250, default 250 (`config.LOOP_MAX`): people per **Launcher run** |
| `launcher_people_cap` | derived | `min(people_max or 30, 30)` |
| `plugin_people_max` | derived | = `people_max` |
| `temporary_enabled`, `temporary_allow`, `temporary_units`, `temporary_max_days` | `temporaryAccess.*` | `allow` ⊆ `("duration","endDate")`, `units` ⊆ `HOURS/DAYS/WEEKS/MONTHS`, `maxDays` null or ≥ 1 |
| `plugin_temporary_modes` / `launcher_temporary_modes` | derived | the Launcher drops `endDate`. In launcher submit mode the plugin offers nothing temporary unless `launcher_form_has_duration` (its end date travels as hours in the form's duration fields) |
| `deploy_launcher`, `deploy_plugin`, `deployments` | `deployments.*` | at least one must be true |
| `plugin_public` | `plugin.public` | default false; the installer pushes `--private` unless this or `--public` is set |
| `plugin_submit` | `plugin.submit` | `"direct"` (default) or `"launcher"` (`config.PLUGIN_SUBMIT_MODES`); see §3 and §9 |
| `plugin_submits_directly` / `plugin_submits_via_launcher` | derived | `deploy_plugin` and the mode |
| `launcher_access_profile_name` | derived | `"{base_name} - Launcher Access"`: the access profile that wraps the Launcher's `assignedLaunchers` entitlement |
| `submit_role_name` | derived | `"{base_name} - Submit Access"`: the requestable role whose holders may submit from the plugin (§3). With the Launcher deployed it contains the Launcher Access profile |
| `submit_access_name` | derived | what people request to make bulk requests: `submit_role_name` when `deploy_plugin`, else `launcher_access_profile_name`. The catalog never offers either |
| `launcher_access_requestable` | derived | `not deploy_plugin`: with the plugin the Launcher Access profile comes through the role, so it isn't requestable on its own |
| `old_plugin_workflow_name` | derived | `"{base_name} (Plugin)"`: the workflow the removed test-endpoint mode used; `plugin/install.py` and `status.py` point it out, `uninstall --only plugin` removes it |
| `launcher_form_has_duration` | derived | the Launcher form gets its duration fields (durations allowed and a unit fits `maxDays`) |
| `access_approval` | `access.approval` | `"MANAGER"` (default) or `"NONE"`: who approves a request for the role, or without the plugin for the Launcher Access profile. Otherwise `` `access.approval` must be "MANAGER" or "NONE". `` The old `access.launcherApproval` and `launcher.accessApproval` still work, each adding a note to `cfg.deprecations` (`` `access.launcherApproval` is now `access.approval`. ``) |
| `approvals_*` | `approvals.*` | the plugin's Approvals tab: `enabled` (default true), `concurrency` 1..8 (4), `useBulkEndpoint` `"auto"`/`"always"`/`"never"`, `maxRows` 250..20000 (5000), `showOther` (false), `denyCommentRequired` (true); see `docs/dev/BULK_APPROVALS_DESIGN.md` |
| `plugin_approvals_enabled` | derived | `deploy_plugin and approvals_enabled` |
| `help_contact` | `notifications.helpContact` | null or text ≤ 300 characters (whitespace collapsed): the emails' *Need help?* line |
| `ui_base_url_override` | `notifications.uiBaseUrl` | null or an http(s) URL without query, `#`, `$`, quotes or spaces (trailing `/` dropped) |
| `config.ui_base_url(cfg, api_base_url)` | derived | the override, else the API host without its `api` label (`acme.api.identitynow.com` → `acme.identitynow.com`, same for `identitynow-demo.com`); `None` when neither gives one |

**Removed settings** still load, are ignored, and each adds a note to `cfg.deprecations` (`show-config` prints them):
`approval` (the whole block), `notifications.ccApprover`, `notifications.pendingEmail` →
`` `<key>` is no longer used: bulk requests have no approval of their own; each item is approved the way SailPoint has it set up. Remove it from the config. ``
(`config.REMOVED_BULK_APPROVAL`). `plugin.submit: "test-endpoint"` becomes `"direct"` with the note
`` `plugin.submit` "test-endpoint" is now "direct": the plugin files the access requests itself, so it no longer needs its own workflow. ``

The `approvals` block may be missing (older configs): every key takes its default. Problems (exact text, mirrored in
`runtime-config.ts`):
- not an object: `` `approvals` must be an object. ``
- a flag that isn't a boolean: `` `approvals.{enabled|showOther|denyCommentRequired}` must be true or false. ``
- `` `approvals.concurrency` must be a whole number between 1 and 8. ``
- `` `approvals.useBulkEndpoint` must be "auto", "always" or "never". ``
- `` `approvals.maxRows` must be a whole number between 250 and 20000. ``

`plugin.submit` problems (exact text): `` `plugin.submit` must be "direct" or "launcher". `` and, for `"launcher"` without
the Launcher deployment, `` `plugin.submit` "launcher" needs the Launcher deployment: set `deployments.launcher` to true, or use "direct". ``

`show-config` prints a `plugin:` line for the role (`role '<name>' (contains '<Launcher Access profile>')` with the Launcher),
a `Submit:` line (how the plugin submits; who may: `holders of the role '<role>' can submit`), an `Access:` line
(`people request '<submit_access_name>' in the Request Center; approval: their manager|none, auto-approved`), an
`Approval:` line (each item the way it's set up in SailPoint; items with no approval are granted right away), one
`Approvals:` line and, when `plugin_approvals_enabled` and the plugin is private, the warning
`non-admin approvers can't open a private plugin unless they are listed in the plugin's restrictToUsers`.

Constants in `config.py`: `FORM_SELECT_MAX = 30`, `LOOP_MAX = 250`, `ACCESS_REQUEST_PEOPLE_MAX = 10`,
`DURATION_UNITS = {"HOURS":"h","DAYS":"d","WEEKS":"w","MONTHS":"M"}`, `UNIT_MAX_DAYS = {"HOURS":1/24,"DAYS":1,"WEEKS":7,"MONTHS":31}`.

## 2. Rules (Python `core/bulkaccess/rules.py`, mirrored in the plugin's `src/app/bulk/rules.ts`)
Messages must match **exactly** in both languages.
- **Request** (`validate_request(cfg, people=, items=, inc=, justification=)` / `validateRequest(cfg, {people, items, inc, justification})`),
  problems in this order:
  - `Choose at least one person.`
  - `Choose at most {max} people.` (only when `people_max` is set; duplicates count once)
  - `Choose at least one access item.`
  - `Choose at most {itemsMax} access items.`
  - `One of the chosen items is not an allowed type.`
  - the configured `inc.message` when the INC doesn't match `inc.pattern`
  - `Enter a business justification.` (`MSG_JUSTIFICATION`; empty or whitespace)
  - `Keep the justification to 1000 characters or fewer.` (`MSG_JUSTIFICATION_LONG`; `JUSTIFICATION_MAX = 1000`, trimmed length)

  There is no approver rule: requesting access for yourself is allowed (SailPoint reassigns a self-approval to an admin).
- **No approval:** `NO_APPROVAL_LABEL = "No approval: granted right away"`. `no_approval_ids(objects, entitlement_default)`:
  an access profile or role with no `approvalSchemes`, or an entitlement with none of its own while the tenant default
  (`entitlementRequestConfig.grantRequestApprovalSchemes`) is empty or `"none"`. `catalog_options(cfg, requestable,
  no_approval=…)` / `catalogOptions(cfg, …)` (from `cfg.noApproval`) append ` · No approval: granted right away` to those
  items' sub-label.
- **Parts:** `split_into_parts(people, part_size)` / `splitIntoParts(people, partSize)`. The deduplicated list in order, chunked to
  `part_size`. `part_label(i, n)` / `partLabel(i, n)` = `""` when n == 1, else `" (i/n)"` (leading space, 1-based). Parts
  are Launcher runs (`partSize`, ≤ 250) or, in direct mode, access requests of 10 people.
- **Temporary access:** the requester picks one of
  - `permanent` → `removeDuration ""`, `removeDate null`, label `Permanent`
  - `duration` `{n, unit}` → `removeDuration f"{n}{DURATION_UNITS[unit]}"`, label `Temporary: {n} {unit word}` (`1 day`, `30 days`, `2 hours`, `1 week`, `3 months`)
  - `endDate` `YYYY-MM-DD` (plugin only) → hours = ceil((end of that day 23:59:59 in the user's local time − now) / 1 h), `removeDuration f"{hours}h"`,
    label `Temporary: until {YYYY-MM-DD}`

  Direct requests carry an ISO `removeDate` instead (`removeDate(choice, now)`, plugin only): now + n hours / days / weeks;
  months as calendar months in UTC, clamped to the target month's last day (31 Jan + 1 month → 28 Feb); an end date →
  23:59:59 local time that day.

  Problems (exact text):
  - Not enabled, or the mode isn't allowed on this route: `Temporary access isn't available.`
  - n not a whole number ≥ 1: `Enter the duration as a whole number of 1 or more.`
  - Unit not in `temporary_units`: `Choose a unit for the duration.`
  - End date not after today: `Choose an end date after today.`
  - Over the cap (n × UNIT_MAX_DAYS[unit], or hours / 24, greater than maxDays): `Temporary access can last at most {maxDays} days.`
- **Launcher form fields:** `launcher_form_access(remove_duration)` / `launcherFormAccess(removeDuration)` turn a validated
  `removeDuration` into the Launcher form's `{accessType, duration, durationUnit}` (§9); anything else raises `ValueError` / `RangeError`.
- **Bulk item comment:** `COMMENT_SEPARATOR = " | "`. `bulk_comment(inc, requester, access_label, justification)` /
  `bulkComment(…)` writes the comment of §4 (INC and justification trimmed). `parse_bulk_comment(cfg, text)` /
  `parseBulkComment(cfg, text)` reads it back as `{inc, requester, approver, accessLabel, justification}`, or `None` / `null`:
  - the shape `<INC> | Bulk access request by <requester> | <access label> | <justification>` → `approver` `None` / `null`;
  - the old shape `<INC> | Bulk access request by <requester> | Approved by <approver> | <access label> | <justification>`
    (written while bulk requests had an approver) is still read, with `approver` set; a third field starting with
    `Approved by` must name someone, else the comment is ignored;
  - the INC must match the configured INC pattern (`inc_is_valid` / `incIsValid`);
  - requester and access label must not be empty; the justification may be empty;
  - the justification is everything after the last fixed field, so it may itself contain ` | ` (and newlines);
  - extra whitespace around the separators, between the label words and at both ends is tolerated (fields are trimmed);
  - not a string, plain comments, the INC alone, 3 fields, wrong or reordered labels → `None` / `null`.
  `definitions.py` builds the Launcher's comment from `COMMENT_SEPARATOR` and `REQUESTER_LABEL`, and a test checks that the
  rendered comment parses back to its inputs.

## 3. Submitting directly (`plugin.submit: "direct"`, the default)
The plugin files the access requests itself, **as the signed-in user**: no workflow, no ORG_ADMIN. The requester on every
request is the person who asked, so ISC's self-approval protection works and they can follow their requests.
`plugin/install.py` checks `GET /access-request-config/v1` → `requestOnBehalfOfConfig.allowRequestOnBehalfOfAnyoneByAnyone`;
when the tenant doesn't allow it, it writes `submit: "launcher"` (or stops with an error when the Launcher isn't deployed).

1. **Gate:** `GET /requestable-objects/v1?identity-id=<me>&types=ROLE&limit=1&filters=id eq "<submitAccessId>"`
   (`BulkApiService.hasSubmitAccess`); `requestStatus` other than `ASSIGNED` → `To submit, you need '{submitAccessName}'. Request it in the Request Center.`
   (`errors.msgNeedsSubmitAccess`) and nothing is sent. No `submitAccessId` in the runtime config (a dry-run install that
   would create the role, or the committed copy) → no check. A failed check →
   `Couldn't check that you may submit: …`.
2. **Requests:** the deduplicated people in parts of 10 (`ACCESS_REQUEST_PEOPLE_MAX`), at most 3 in flight:
   ```json
   POST /access-requests/v1
   { "requestedFor": ["<identityId>", "…"],          // 1..10
     "requestType": "GRANT_ACCESS",
     "requestedItems": [{"type": "ACCESS_PROFILE", "id": "…", "comment": "<bulk comment §4>",
                         "removeDate": "2026-11-07T10:00:00.000Z"}] }   // removeDate only for temporary access
   ```
   Result per part: `created` = `newRequests.length`, `existing` = `existingRequests.length` (already open, not requested again).
3. **Errors:** a 401/403 → `SailPoint refused the request (HTTP {status}). Your account may not be allowed to request access for these people (the tenant's "request for others" settings), or your session expired: reload the page and try again.`
   and the parts not yet sent fail with the same message; any other error fails only its part (retry sends just the failed ones).
4. **Dry-run mode:** nothing is sent; each part is marked checked.
5. **States:** part `queued → sending → requested | failed` (`stopped` in launcher mode only); submission `sending`,
   `requested`, `partial`, `error`. Message: `Filed {n} access requests[ ({k} already pending, so not requested again)]. Each is approved the way it's set up in SailPoint; follow them on My bulk requests.`
6. **My bulk requests:** `GET /access-request-status/v1?requested-by=<me>&limit=250&offset=N&sorters=-created` (up to 1000
   rows, `my-requests.ts`), grouped by the INC of `parseBulkComment` (or `extractInc`), rows by someone else skipped. Per request:
   `REQUEST_COMPLETED` → done; `REJECTED`, `CANCELLED`, `TERMINATED`, `ERROR`, `PROVISIONING_FAILED` → not granted; a
   `PENDING` entry in `approvalDetails` → waiting for its `currentOwner.name`; else in progress. Requests filed by the
   Launcher's workflow have its owner as requester, so they don't show here.

## 4. What every request carries, and the Launcher workflow (`core/bulkaccess/definitions.py`)
- **Item comment** (both submit modes and the Launchpad): `{inc} | Bulk access request by {requester display name} | {accessLabel} | {justification}`.
  The INC stays first: My bulk requests, the Approvals tab and `plugin/status.py` group by it.
- **Launcher workflow** (one run per Launcher submission; the plugin sends one run per part in launcher mode):
  `Interactive Form → Get Requester → INC Valid? → Define Variable Access → Part Given? (→ Set Part) → [Temporary? →
  Duration Valid? → Unit Valid? → Set Temporary Access → (Within Limit?)] → Notify Requested → (live) Request Access →
  Email Requested → End Step - Success`. A failed check goes to a *Reject …* step (`Reject Bad INC`, `Reject Bad Duration`,
  `Reject Bad Unit`, `Reject Too Long`): an ERROR interactive message, then *Email Reject …*, then `End Step - Rejected`.
- **Notify Requested:** an INFO interactive message, title `Requested: {inc}{partLabel}` (dry run: `Checked: {inc}{partLabel}`).
  The plugin reads it back (§9).
- **Manage Access:** `sp:access:manage` `versionNumber: 2` inside `sp:loop:iterator` over the people (`context.$: "$"`,
  `$.loop.context.…` paths): `addIdentities` = one person, `requestedItems` = every item, `removeDuration` from the access
  variables, `comments` = the item comment. Not built in dry-run mode.
- **Launcher form fields** (keys): `people`, `items`, `inc` (REGEX), `justification` (TEXTAREA, no length rule), and, when `launcher_temporary_modes` contains `duration`: `accessType` (TOGGLE, Permanent/Temporary), `duration`
  (whole number), `durationUnit` (from `temporary_units`). Items without an approval of their own carry the no-approval
  sub-label. The Launcher's access label is `Temporary: {n}{suffix}` (e.g. `Temporary: 30d`).
- **Emails:** see §10.

## 5. Plugin runtime config (`public/bulk-access.config.json`, written by `plugin/pluginlib.py`)
`"submit": "direct" | "launcher"`, `"launcherId": string | null` (launcher mode only: looked up by name at install, since a
non-admin can't list launchers), `"submitAccessId": string | null` (the Submit Access role; the submit gate),
`"submitAccessName": string` (the role's name), `"noApproval": [ids]` (catalog items with no approval of their own, worked out at every
`apply` by `catalog.no_approval_ids`, sorted).
`runtime-config.ts`: no `submit` (older install) or `"test-endpoint"` → `"direct"`; other values → `submit must be
"direct" or "launcher".`; `"launcher"` without `launcherId` loads, but submitting stops with `MSG_LAUNCHER_ID`
("…launcherId is missing: run plugin/install.py…"). `noApproval` that isn't a list of strings → `noApproval must be a list of item IDs.`
There is no `workflowName` / `workflowId` any more.

Other fields: `"peopleMax": null | number`, `"partSize": number`, `"itemsMax"`, `"catalogTypes"`, `"nameStartsWith"`,
`"launcherName"`, `"temporary": {"enabled": bool, "allow": ["duration","endDate"], "units": ["HOURS",...], "maxDays": null | number}`
(`enabled`/`allow` carry `plugin_temporary_modes`). `runtime-config.ts` validates: peopleMax null or ≥ 1; partSize 1..250;
allow/units from the fixed lists.

`"approvals": {"enabled": bool, "concurrency": 1..8, "useBulkEndpoint": "auto"|"always"|"never", "maxRows": 250..20000,
"showOther": bool, "denyCommentRequired": bool}`. `enabled` carries the derived `plugin_approvals_enabled`. `runtime-config.ts`
(`ApprovalsConfig`) applies the same ranges with the same messages as §1. A file **without** the block comes from an older
install, so the tab is off (`enabled: false`, other keys at their defaults); a block with missing keys gets the Python defaults
(`enabled: true`).

## 6. One CLI (`bulkaccess.py` at the repo root)
```
python bulkaccess.py show-config --config config/<tenant>.json
python bulkaccess.py apply       --config … [--dry-run] [--only launcher|plugin] [--deploy] [--grant me|<ids>]
python bulkaccess.py status      --config … [--only …]
python bulkaccess.py uninstall   --config … [--only …] [--yes]
python bulkaccess.py export      --config … [--only …] [--offline] [--out DIR]
```
`apply` runs the Launcher before the plugin: in launcher submit mode `plugin/install.py` looks the Launcher up by name and stops
with a clear error when it's missing. It runs each enabled deployment by calling the `launcher/*.py` and `plugin/*.py`
`main(argv)` functions, loaded by file path (both folders have an `install.py`). Those scripts keep working on their own.
`plugin/install.py` takes its `--public` default from `cfg.plugin_public`. Both installers print the catalog items with no
approval of their own. `launcher/status.py` shows the installed objects (no run list: `GET /workflows/{id}/executions` is
deprecated and has no `/v1` list); `plugin/status.py` shows the submit mode, a leftover old plugin workflow, and the pending
item approvals of bulk requests grouped by INC.

**Who may make bulk requests** (`core/bulkaccess/submit_access.py`, `definitions.submit_role` / `role_changes`):
- `plugin/install.py` creates the requestable role `submit_role_name` (`POST /roles/v1`, `accessProfiles` = the Launcher
  Access profile when the Launcher is deployed, else `[]`; `entitlements: []`; approval per `access_approval`), or PATCHes
  only what differs (description, `accessProfiles`, `requestable`, `accessRequestConfig`). The Launcher Access profile gets
  `requestable: launcher_access_requestable`.
- **Migration:** holders of the Launcher Access profile (`POST /search/v1`, `@access(id:"<id>")`) who don't hold the role
  get an access request for it (10 per request, approved per `access_approval`); a dry run lists them.
- `--grant me|<ids>` requests `submit_access_name`: `bulkaccess.py apply` passes it to `plugin/install.py` (the role) when
  `deploy_plugin`, otherwise to `launcher/install.py` (the profile).
- `plugin/status.py` prints `Who may submit: holders of the role '<role>' (<id>, N holder(s))`, or the role as MISSING;
  `launcher/status.py` marks the profile `!!` when its `requestable` isn't `launcher_access_requestable`.
- `plugin/uninstall.py` deletes the role. `launcher/uninstall.py` first PATCHes the Launcher Access profile out of the role
  (the role stays), then deletes as before. `uninstall --only <d>` also works for a deployment switched off in
  `deployments`, so retiring the Launcher is: `deployments.launcher: false`, `apply`, `uninstall --only launcher --yes`.
- `export` also writes `plugin/submit-role.json` (offline, the profile ID is `<LAUNCHER_ACCESS_PROFILE_ID>`).

Every API path lives in one module per language: `core/bulkaccess/api.py` and `plugin/src/app/bulk/api-paths.ts`. All are
GA `/<service>/v1` paths, except `GET /beta/interactive-processes/{id}/blocks` (launcher submit mode only; no GA path) and
the experimental `/ui-plugins/v1` (with `X-SailPoint-Experimental: true`). Workflow run history: `GET /workflow-executions/v1/{id}/history-v2`.

## 7. The plugin's Approvals tab: list, decide, confirm
Item approvers decide the per-person approvals a bulk request created, one INC at a time. Built in `bulk-api.service.ts`
(`pendingAccessApprovals`, `decideApprovals`, `approvalStatuses`), `approvals.ts` and `approvals-store.ts`; settings from §5
`approvals`. Facts verified live as a non-admin (2026-10-08, re-checked on `/v1`) are marked ✔.
- **List:** `GET /generic-approvals/v1?mine=true&include-comments=true&limit=250&offset=N&sorters=createdDate`
  `&filters=status eq "PENDING" and type eq "ACCESS_REQUEST_APPROVAL"` (URL-encoded), paged up to `maxRows`; "more" is shown
  when the cap is hit. `mine=true` keeps admins to their own (a non-admin only ever gets their own ✔).
- **Quirk ✔:** with `include-comments=true` rows carry `comments[]` but **no `assignedTo`**. Never rely on `assignedTo`.
- **Grouping:** by `parse_bulk_comment` / `parseBulkComment` (§2, both comment forms) over each row's `comments[]`; rows
  without a bulk comment form one *Other* group, shown only with `showOther`, and **view-only**. An action never spans INCs.
  The group's requester is the comment's (direct requests also have the real requester on the row; Launcher ones the workflow owner).
- **Default path (everyone):** `POST /generic-approvals/v1/{id}/approve|reject`, body `{comment}` (or `{}` with no comment).
  Approve ✔ and reject ✔ both answer 200 with the approval (the spec says 204 for reject; accept either). `concurrency`
  (1..8) calls in flight, all generic-approvals calls of the tab spaced to **≤ 8 per second** (limit: 100 per 10 s per
  client and API version). 429 and 5xx are retried (4 attempts, growing back-off; the plugin SDK hides `Retry-After`).
  A non-admin deciding someone else's approval gets 403 ✔.
- **Bulk path:** `POST /generic-approvals/v1/bulk-approve|bulk-reject` `{approvalIds (≤ 50), comment?}` → `202 {}` even for
  unknown IDs. Non-admins get **403 even for their own approvals ✔**, so it is used only when `useBulkEndpoint` is
  `always`, or `auto` and the user is ORG_ADMIN. A 401/403 switches that batch and the rest to the default path.
- **Deny** needs a non-empty comment when `denyCommentRequired`.
- **Confirm:** a 2xx or 202 proves nothing. Re-read with `GET /generic-approvals/v1?limit=250&filters=approvalId in ("a",…)`
  (50 IDs per call, no `mine`), after 0, 1, 2, 4 and 8 s. The list has `status` but **empty `approvedBy`/`rejectedBy` ✔**;
  only `GET /generic-approvals/v1/{id}` names the decider, so it is called only for IDs whose status changed although
  our call failed. Outcome per ID:
  | Outcome | When |
  |---|---|
  | `confirmed` (decided by me) | status = APPROVED/REJECTED as sent and our call succeeded, or the detail names the caller |
  | `elsewhere` (decided by someone else) | any other decided status, or the ID is no longer returned. A governance-group colleague or an admin decided first: **not an error** |
  | `pending` (still pending) | our call succeeded but the last re-read still says PENDING; can be retried |
  | `failed` | our call failed and it's still PENDING; can be retried, with the reason |
- After a run, `confirmed` and `elsewhere` rows leave the list; a later approval step of a serial scheme appears on refresh.

## 8. Where the catalog and the people come from
- **Catalog, access profiles and roles:** `GET /requestable-objects/v1?identity-id=<signed-in user>&types=…&types=…&limit=250&offset=N`
  (a non-admin gets **403 without `identity-id`** and 200 with their own ✔ non-admin; the installer's PAT calls it without), with only the
  configured types among `ACCESS_PROFILE`/`ROLE` (`rules.requestable_object_types` / `requestableObjectTypes`); not called
  when none is configured (no `types` means every type). `ENTITLEMENT` is outside its `types` enum: alone → 400, next to
  another type → silently dropped.
- **Catalog, entitlements** (when `catalog.types` contains `ENTITLEMENT`): `GET /entitlements/v1?filters=<f>&sorters=name&limit=250&offset=N`,
  where `<f>` = `rules.entitlement_filter(cfg)` / `entitlementFilter(cfg)`:
  `requestable eq true`, plus ` and name sw "<nameStartsWith>"` (quoted like every filter value: `"` and `\` escaped).
  Rows have `source.name` and `description` but **no `type`**: the caller adds `type: "ENTITLEMENT"` before `catalog_options`,
  so the item sent stays `{id, type: "ENTITLEMENT", name}`.
- **Approval setup (installers, `core/bulkaccess/catalog.py`):** access profiles and roles by `GET /access-profiles/v1` /
  `GET /roles/v1` `?filters=id in (…)` (50 per call), `accessRequestConfig.approvalSchemes`; each entitlement's
  `GET /entitlements/v1/{id}/entitlement-request-config`; the tenant default from `GET /access-request-config/v1`
  (`entitlementRequestConfig.grantRequestApprovalSchemes`). → `noApproval` (§5) and the Launcher form's sub-labels.
- **"Already has it" (plugin review step, best effort, ≤ 100 people; run each time the step is shown; a failed call is
  skipped and the step says "Couldn't check who already has this access: …"):**
  - access profiles and roles: `GET /requestable-objects/v1?identity-id=<id>&types=…&filters=id in (…)` → `requestStatus`
    `ASSIGNED` / `PENDING`. A non-admin may pass **another person's** `identity-id` and gets their status ✔ non-admin;
  - entitlements are checked **for ORG_ADMIN only** (`entitlementsChecked()`): for a non-admin identity search is 403 and another
    person's `access-request-status` is 400 "must be the current user" ✔ non-admin. The review step then says, in a muted line,
    that entitlements aren't checked;
  - entitlements, held: `POST /search/v1?limit=250` `{indices: ["identities"], query: {query: "id:(<id> OR …)"}, queryResultFilter:
    {includes: ["id","access.id","access.type"]}}`, 100 people per call; a chosen entitlement in `access[]` → `ASSIGNED`.
    Identities missing from the search index get no warning;
  - entitlements, pending: `GET /access-request-status/v1?requested-for=<id>&request-state=EXECUTING&limit=250`; a row with
    `type: ENTITLEMENT`, a chosen `id` (the row `id` is the item's ID) and not `requestType: REVOKE_ACCESS` → `PENDING`;
  - one entry per person and item; `ASSIGNED` wins.
- **People (search and pasted lists).** ORG_ADMIN: `/identities/v1`, identity search (`/search/v1`) and `/accounts/v1` (with
  fallbacks for identities not indexed yet). Everyone else (those three are **403** ✔ non-admin): `GET /public-identities/v1?limit=…&sorters=name&filters=…`
  ✔ non-admin. Type-ahead: `displayName sw v or alias sw v or email sw v or firstname sw v or lastname sw v`; pasted IDs: `id in (…)`
  (50 per call); pasted words: `alias eq v or email eq v`. Matching is case-insensitive. `name`/`status` filters and `co`/`in` (except
  on `id`) are 400; `limit` ≤ 250. Rows: `id, name, alias, email, attributes[{key,value}]` (department from `attributes`, no
  display name). No fallbacks: someone public identities doesn't list stays unresolved (paste their ID, or ask an admin).
- The Launcher form's `items` SELECT keeps `maximum: catalog.maxItems` (≤ 25, under the 30-selection limit).

## 9. Submitting through the Launcher (`plugin.submit: "launcher"`, the fallback)
The plugin drives the Launcher deployment as the **signed-in user**, so anyone holding the Submit Access role (which
contains the Launcher Access profile) can submit.
The Launcher's workflow then files the requests, with the **workflow owner** as requester. Verified live on 2026-10-08 as a
real **non-admin** with Launcher Access, using their own ISC UI session token (✔), and with the admin PAT (✔ PAT).

**Per part** (`partSize` people, ≤ 250; `bulk-api.service.ts` `launch`, `launcherFormInstance`, `submitLauncherForm`;
`request-store.ts` `sendViaLauncher`), after the gate of §3:
1. `POST /launchers/v1/{launcherId}/launch` body `{}` → `200 {"interactiveProcessId": "<ULID>"}` ✔. A non-admin **can't list**
   launchers (500 "insufficient authorization") but can GET one by ID ✔, so `plugin/install.py` writes `launcherId` (§5).
2. `GET /beta/interactive-processes/{ipid}/blocks` → `{"items": [{"type": "FORM", "config": {"formInstanceId": "<uuid>"},
   "data": {"title", "message"}, "id", "created"}]}` ✔, polled every second until the FORM block appears, for at most 30 s
   (then "…its form didn't appear within 30 seconds…"). **Beta**, with no GA equivalent (`/interactive-processes/v1` → 401 for
   the PAT); the admin PAT (client credentials) gets **401** on it ✔ PAT, so it only works with a user session. A non-admin can't
   list form instances (403) but can GET one by ID ✔.
3. `PATCH /form-instances/v1/{id}` (`application/json-patch+json`)
   `[{"op":"replace","path":"/formData","value":{…}},{"op":"replace","path":"/state","value":"SUBMITTED"}]` → 200 ✔; one PATCH
   went ASSIGNED → COMPLETED. Repeated (up to 3) until `SUBMITTED`/`COMPLETED` or `formErrors` (then the part fails with
   `The Launcher form refused the request ({key}: {message}). Nothing was requested.`). The plugin SDK has no PATCH, so
   `SailpointPluginService.patch` calls `fetch` with the SDK's token and the tenant's `apiUrl.idn`.
4. **The answer:** the process's blocks are read again (2, 2, 3, 3, then every 5 s, for 2 minutes): an INFO message block
   (*Notify Requested*, §4) → part `requested` with its message (without the title, which repeats the page's heading;
   the INFO block was found this way live ✔); an ERROR block (a *Reject …* step) → part `stopped`,
   `The Launcher stopped it: {title}: {message}`. No answer within 2 minutes → `requested`, with "Submitted to the Launcher,
   which hasn't answered yet. You'll get an email with the result." The category is looked for in `config` and `data`.

**formData** (the Launchpad's own shapes, `LauncherFormData`): `people` (identity IDs; 40 and 250 accepted ✔: the 30 cap is the
UI picker's only), `items` (`{id,type,name}`; an item outside the form's STATIC options is accepted ✔), `inc`, `justification`,
`accessType` (boolean), `duration` (string), `durationUnit` (one-item list of the suffix, or `[]`), `partLabel` (`""` or `" (k/n)"`).
`rules.launcher_form_access` / `launcherFormAccess` map a validated `removeDuration`: `""` → `false, "", []`; `"30d"` →
`true, "30", ["d"]`; an end date → hours, `"720h"` → `true, "720", ["h"]`. A unit outside the form's options is accepted by the
form ✔ PAT; the Launcher workflow accepts hours whenever the plugin offers an end date (`definitions.launcher_workflow_units`).
Unknown formData keys are kept ✔ PAT. The form's REGEX rules apply on submit, so the plugin validates first (rules.ts).

**The part label:** an extra formData key, `partLabel`, with no field on the form (a `HIDDEN` element makes the Launchpad
render an empty form ✔). The workflow defines `partLabel` as `"single"` emptied by a replace transform (in *Define Variable
Access*), then *Part Given?* (`StringMatches` `PART_LABEL_REGEX` `^ \([1-9][0-9]*/[1-9][0-9]*\)$` on that key) → *Set Part*.
A missing key (Launchpad) or a malformed one (`" (1/2) evil"`) leaves it empty ✔ PAT.

**Errors:** a 401/403, or a 500 whose message says "insufficient authorization", on any of these calls →
`To submit, you need '{submitAccessName}'. Request it in the Request Center.` (`errors.ts` `describeSubmitError`),
and the remaining parts are not tried.

## 10. Emails (`definitions.py`: `requested_email`, `rejected_email`; Launcher workflow only)
Verified live on 2026-10-09 (demo tenant, `sp:send-email` v2):
- The body is a **Velocity** template, rendered by SailPoint's email service with the step's `context` map
  (`#if`/`#elseif`/`#foreach`/`#set`, `$!{x}`, `#{else}`/`#{end}` and list literals work). Text that isn't valid Velocity
  (e.g. a justification with `#if(` templated into the body) makes the send **400 Bad Request** and **fails the run**.
- `context` values: `"key.$": "<JSONPath>"` arrives intact (newlines, quotes, `#`, `$`). A `"{{…}}"` template value is
  spliced in raw, so a newline, `"` or `\` in it fails the send with the same 400. A missing JSONPath leaves the variable
  unset (`$!{key}` prints nothing); a missing `{{…}}` path renders `""`. (In the body or subject, `{{…}}` with a newline
  is fine.)
- One-item lists arrive **unwrapped** in the context (the item itself); `$.list.length()` still counts right.
  `{{$.list[*].name}}` renders Go-style (`["a" "b"]`), so lists go through the context and `#foreach`.
- Undefined references don't fail (non-strict). The run history shows each send-email step's template and resolved
  context, not the rendered mail. To check rendering, probes made the send fail only when the rendering matched an
  expected string: `#define($block)…#end#if($expected == "$block")#evaluate($bad)#end` with `bad = "#if("` in the context.

What the emails do with that:
- The body is **constant markup** (no `{{…}}`, no `##`, no `#` outside directives, colours as `rgb()`), and every value travels
  in the context as **JSONPath** (`"key.$"`). Config text goes in as plain context values. `core/tests/velocity_lite.py`
  renders a step the way SailPoint does.
- **Layout:** a coloured title bar, a DRY RUN banner in `dry-run`, then sections **What** (INC and part, each item as
  "name (access profile|role|entitlement)", "N people" / "1 person", how long), **Why** (justification, requested by),
  **What happens next** / **What to do now**, and **Need help?** (`help_html(cfg)`: `helpContact` escaped, web and email
  addresses linked, default "Contact your SailPoint administrator."). One table, inline styles, max 640 px wide.
- **Steps** (to the requester; everything to `overrideRecipients` when set):
  | Step | When | Subject |
  |---|---|---|
  | `Email Requested` | after *Request Access* (live) or after the checks (dry run) | `Requested: bulk access {inc}{part}` / `Checked: bulk access {inc}{part} (DRY RUN: nothing was requested)` |
  | `Email Reject …` | after a *Reject …* step's Launchpad message | `Not requested: {title} ({base_name})` (no INC: it may be the bad value) |
- **Requested** (live) says each item is now approved the way it's set up in SailPoint (items without an approval of their
  own are granted right away), where item approvers decide (Approvals → Access Requests, or the plugin's Approvals tab when
  `plugin_approvals_enabled`), that the requests are filed by the workflow so they're not in the requester's Request Center,
  and that temporary access ends by itself. Dry run: nothing was requested.
- **Rejections name the field:** bad INC → *ServiceNow incident (INC) number* (shows the value entered); duration → *Duration* /
  *Unit* (shows the value entered); over `maxDays` → *Duration*. The Launchpad ERROR message is
  `<p>{field}: {problem}</p><p>Nothing was requested. Fix it and submit again; you'll also get this by email.</p>`.
- **Links** (`definitions.EmailLinks(ui, plugin_id)`, built by the installer): Approvals → Access Requests
  `/ui/d/approvals/access-request/requested-items`, Launchpad `/ui/d/launchpad`, the plugin `/ui/plugin/{pluginInstanceId}`
  (only with `deploy_plugin`). The plugin ID comes from `GET /ui-plugins/v1/resolve-alias?alias=<plugin.alias>` with
  `X-SailPoint-Experimental: true` (200 with `pluginInstanceId`, 404 for an unknown alias; works with the PAT). The Launcher
  installs first, so its emails get plugin links from the `apply` after the plugin's first upload; without a UI address the
  emails name the pages without links.
- Direct submissions (§3) send no email of their own: SailPoint's usual access-request notifications apply.

## Test data and safety (live tests in a test or shared tenant)
- Only touch objects named with the config's prefix. Use a harmless test access profile as the only catalog item
  (`catalog.nameStartsWith`) and test identities as the people.
- Each live grant uses a fresh test identity; the brief lists which ones.
- **Every request a test creates must end:** approve or reject its item approvals, or cancel it (§ Verified live). Never leave
  one pending.
- Leave every installation in `mode: dry-run` when you finish.
- No secrets in files or output. Never `sail --debug`. No `sed -i` (it breaks file permissions on this mount).
