# Contracts: no people limit, temporary access, one central config

This is the shared spec for the work split across three agents (core/Launcher, UI plugin, docs). **Build against this
file, not against another agent's code.** If something here turns out to be wrong or impossible, stop and report
it to the orchestrator instead of changing it alone.

## Verified live (2026-10-08, test tenant, spikes)
| Fact | Result |
|---|---|
| `sp:loop:iterator` (Loop) size limit | **250 items**, hard: above that the step fails with "Input has N iterations which exceed 250 iteration limit". 250 ran in about 13 s, in parallel. |
| `sp:serial:iterator` (Serial Loop) | **Not usable.** It silently stops after **50** items with no error (and stops at the first failing item). Don't use it. |
| Loop shape | No change: the existing `sp:loop:iterator` with `context.$: "$"` and `$.loop.context.…` paths stays. |
| Manage Access **versionNumber 2** | Has `removeDuration` (hours, days, weeks, months) and `startDate`. v1 had days and weeks only, and "not supported for entitlements". **Use v2.** |
| `removeDuration` encoding | A string `"<n><suffix>"`: `"2h"` → removeDate +2 h, `"1d"` → +1 day, `"1w"` → +7 days, `"1M"` → +1 month. |
| Permanent | `removeDuration` **missing** (JSONPath to a missing key) or `""` → permanent (`removeDate: null`). So there's no branching: always pass it. |
| Invalid duration | `"abc"` **fails the step** ("timeext: invalid duration"). Only send validated strings. |
| Entitlements | A temporary entitlement through Manage Access v2 works (removeDate +1 day). Nothing to block. |
| End date on the Launcher | No workflow transform turns a date into a duration, so **the Launcher offers duration only**. The plugin offers duration **and** end date, converting the end date to hours itself. |
| `/v3/access-request-status` rows | `requestedFor` is an object `{id,name,type}`, `name` is the item name, `removeDate` holds the expiry, and `requesterComment.comment` holds our comment. There's no `requestedObject`. |

## 1. The central config (`config/<tenant>.json`)
`core/bulkaccess/config.py` (already on the base branch) is the **only** loader. Schema: `config/bulk-access.example.json`.

| `Config` attribute (Python) | From | Meaning |
|---|---|---|
| `people_max: int \| None` | `people.max` | `None` = no limit (default) |
| `part_size: int` | `people.partSize` | 1..250, default 250 (`config.LOOP_MAX`) |
| `launcher_people_cap` | derived | `min(people_max or 30, 30)` |
| `plugin_people_max` | derived | = `people_max` |
| `temporary_enabled`, `temporary_allow`, `temporary_units`, `temporary_max_days` | `temporaryAccess.*` | `allow` ⊆ `("duration","endDate")`, `units` ⊆ `HOURS/DAYS/WEEKS/MONTHS`, `maxDays` null or ≥ 1 |
| `plugin_temporary_modes` / `launcher_temporary_modes` | derived | the Launcher drops `endDate`. In launcher submit mode the plugin offers nothing temporary unless `launcher_form_has_duration` (its end date travels as hours in the form's duration fields) |
| `deploy_launcher`, `deploy_plugin`, `deployments` | `deployments.*` | at least one must be true |
| `plugin_public` | `plugin.public` | default false; the installer pushes `--private` unless this or `--public` is set |
| `plugin_submit` | `plugin.submit` | `"launcher"` (default when `deployments.launcher` is true) or `"test-endpoint"` (default otherwise), `config.PLUGIN_SUBMIT_MODES`; see §9 |
| `plugin_submits_via_launcher` / `plugin_needs_workflow` | derived | `deploy_plugin` and the mode; the plugin workflow is only installed for `"test-endpoint"` |
| `launcher_access_profile_name` | derived | `"{base_name} - Launcher Access"`: the profile that grants the Launcher (and so, in launcher mode, submitting from the plugin) |
| `launcher_form_has_duration` | derived | the Launcher form gets its duration fields (durations allowed and a unit fits `maxDays`) |
| `launcher_access_approval` | `access.launcherApproval` | the old `launcher.accessApproval` still works (it adds a note to `cfg.deprecations`) |
| `approvals_enabled` | `approvals.enabled` | bool, default true: the plugin's Approvals tab (see `docs/dev/BULK_APPROVALS_DESIGN.md`) |
| `approvals_concurrency` | `approvals.concurrency` | 1..8, default 4: decisions sent at a time |
| `approvals_use_bulk_endpoint` | `approvals.useBulkEndpoint` | `"auto"` (default), `"always"` or `"never"` (`config.BULK_ENDPOINT_MODES`): SailPoint's generic bulk-approve/reject |
| `approvals_max_rows` | `approvals.maxRows` | 250..20000, default 5000: the most pending approvals the tab loads |
| `approvals_show_other` | `approvals.showOther` | bool, default false: also list approvals that aren't from a bulk request |
| `approvals_deny_comment_required` | `approvals.denyCommentRequired` | bool, default true |
| `plugin_approvals_enabled` | derived | `deploy_plugin and approvals_enabled` (the tab lives in the plugin) |
| `pending_email` | `notifications.pendingEmail` | bool, default true: the *Waiting for approval* email (§10) |
| `help_contact` | `notifications.helpContact` | null or text ≤ 300 characters (whitespace collapsed): the emails' *Need help?* line |
| `ui_base_url_override` | `notifications.uiBaseUrl` | null or an http(s) URL without query, `#`, `$`, quotes or spaces (trailing `/` dropped) |
| `config.ui_base_url(cfg, api_base_url)` | derived | the override, else the API host without its `api` label (`acme.api.identitynow.com` → `acme.identitynow.com`, same for `identitynow-demo.com`); `None` when neither gives one |

The `approvals` block may be missing (older configs): every key takes its default. Problems (exact text, mirrored in
`runtime-config.ts`):
- not an object: `` `approvals` must be an object. ``
- a flag that isn't a boolean: `` `approvals.{enabled|showOther|denyCommentRequired}` must be true or false. ``
- `` `approvals.concurrency` must be a whole number between 1 and 8. ``
- `` `approvals.useBulkEndpoint` must be "auto", "always" or "never". ``
- `` `approvals.maxRows` must be a whole number between 250 and 20000. ``

`plugin.submit` problems (exact text): `` `plugin.submit` must be "launcher" or "test-endpoint". `` and, for `"launcher"`
without the Launcher deployment, `` `plugin.submit` "launcher" needs the Launcher deployment: set `deployments.launcher` to true, or use "test-endpoint". ``
`show-config` prints a `Submit:` line saying who can submit from the plugin (holders of the Launcher Access profile, or ORG_ADMIN).

`show-config` prints one `Approvals:` line and, when `plugin_approvals_enabled` and the plugin is private, the warning
`non-admin approvers can't open a private plugin unless they are listed in the plugin's restrictToUsers`.

Constants in `config.py`: `FORM_SELECT_MAX = 30`, `LOOP_MAX = 250`, `DURATION_UNITS = {"HOURS":"h","DAYS":"d","WEEKS":"w","MONTHS":"M"}`,
`UNIT_MAX_DAYS = {"HOURS":1/24,"DAYS":1,"WEEKS":7,"MONTHS":31}`.

## 2. Rules (Python `core/bulkaccess/rules.py`, mirrored in the plugin's `src/app/bulk/rules.ts`)
Messages must match **exactly** in both languages.
- **People:** no upper bound unless `people_max` is set: `Choose at most {max} people.` (existing).
- **Parts:** `split_into_parts(people, part_size)` / `splitIntoParts(people, partSize)`. The deduplicated list in order, chunked to
  `part_size`. `part_label(i, n)` / `partLabel(i, n)` = `""` when n == 1, else `" (i/n)"` (leading space, 1-based).
- **Temporary access:** the requester picks one of
  - `permanent` → `removeDuration ""`, label `Permanent`
  - `duration` `{n, unit}` → `removeDuration f"{n}{DURATION_UNITS[unit]}"`, label `Temporary: {n} {unit word}` (`1 day`, `30 days`, `2 hours`, `1 week`, `3 months`)
  - `endDate` `YYYY-MM-DD` (plugin only) → hours = ceil((end of that day 23:59:59 in the user's local time − now) / 1 h), `removeDuration f"{hours}h"`,
    label `Temporary: until {YYYY-MM-DD}`

  Problems (exact text):
  - Not enabled, or the mode isn't allowed on this route: `Temporary access isn't available.`
  - n not a whole number ≥ 1: `Enter the duration as a whole number of 1 or more.`
  - Unit not in `temporary_units`: `Choose a unit for the duration.`
  - End date not after today: `Choose an end date after today.`
  - Over the cap (n × UNIT_MAX_DAYS[unit], or hours / 24, greater than maxDays): `Temporary access can last at most {maxDays} days.`
- **Launcher form fields:** `launcher_form_access(remove_duration)` / `launcherFormAccess(removeDuration)` turn a validated
  `removeDuration` into the Launcher form's `{accessType, duration, durationUnit}` (§9); anything else raises `ValueError` / `RangeError`.
- **Bulk item comment:** `COMMENT_SEPARATOR = " | "`. `parse_bulk_comment(cfg, text)` / `parseBulkComment(cfg, text)` reads back
  the item comment of §4 and returns `{inc, requester, approver, accessLabel, justification}`, or `None` / `null` for anything else:
  - exactly the shape `<INC> | Bulk access request by <requester> | Approved by <approver> | <access label> | <justification>`;
  - the INC must match the configured INC pattern (`inc_is_valid` / `incIsValid`);
  - requester, approver and access label must not be empty; the justification may be empty;
  - the justification is everything after the 4th separator, so it may itself contain ` | ` (and newlines);
  - extra whitespace around the separators, between the label words and at both ends is tolerated (fields are trimmed);
  - not a string, plain comments, the INC alone, 4 fields, wrong or reordered labels → `None` / `null`.
  `definitions.py` builds the comment from `COMMENT_SEPARATOR`, and a test checks that the rendered comment parses back to its inputs.

## 3. Plugin workflow input (`plugin.submit: "test-endpoint"`; one workflow-test run **per part**)
In launcher mode the same request goes into the Launcher form instead (§9).
Sent as `{input}` to `POST /v2025/workflows/{id}/test`; each run is followed with `GET /v2025/workflow-executions/{id}` (§8).
Every field is **always** present (no missing paths in templates):
```json
{ "people": ["<identityId>", "..."],          // 1..partSize (≤250)
  "items": [{"id": "...", "type": "ACCESS_PROFILE", "name": "..."}],
  "approverId": "...", "requesterId": "...", "inc": "INC0012345", "justification": "...",
  "part": 2, "parts": 3, "partLabel": " (2/3)",   // partLabel "" when parts == 1
  "removeDuration": "30d",                        // "" = permanent
  "accessLabel": "Temporary: 30 days" }           // or "Permanent" / "Temporary: until 2026-11-07"
```
All parts of one submission share the same INC, approver, items, justification and access choice.

## 4. Workflow output (both variants, built by `core/bulkaccess/definitions.py`)
- **Approval name:** `Bulk access {inc}{partLabel}` (≤ 50 characters). On the Launcher, `partLabel` is the hidden form field
  of §9 when it matches `^ \([1-9][0-9]*/[1-9][0-9]*\)$` (`definitions.PART_LABEL_REGEX`), else `""` (the Launchpad leaves it out).
- **Approval description:** `{prefix} bulk access request {inc}{partLabel} from {requester} · {accessLabel}`.
- **Manage Access:** `versionNumber: 2`, inside the existing loop, `removeDuration` from the input (plugin:
  `removeDuration.$: "$.loop.context.trigger.removeDuration"`; the Launcher builds it from its form fields).
- **Item comment:** `{inc} | Bulk access request by {requester} | Approved by {approver} | {accessLabel} | {justification}`. The
  INC stays first, because the plugin's My bulk requests reads it from there.
- **Emails:** see §10.
- **Launcher form, new fields** (keys): `accessType` (Permanent or Temporary, default Permanent), `duration` (a whole number, only used
  when Temporary), `durationUnit` (from `temporary_units`). Only offered when `launcher_temporary_modes` contains `duration`. The
  Launcher's access label can be `Temporary: {n}{suffix}` (e.g. `Temporary: 30d`) if the unit word can't be templated.
  The Launcher must enforce the same rules (whole number ≥ 1, `maxDays`) before the approval, with a clear Launchpad message.

## 5. Plugin runtime config (`public/bulk-access.config.json`, written by `plugin/pluginlib.py`)
`"submit": "launcher" | "test-endpoint"`, `"launcherId": string | null` (launcher mode: the Launcher's ID, looked up by name at
install, since a non-admin can't list launchers), `"launcherAccessName": string`, and `"workflowId"` (test-endpoint mode only).
`runtime-config.ts`: a file without `submit` comes from an older install → `"test-endpoint"`; other values → `submit must be
"launcher" or "test-endpoint".`; `"launcher"` without `launcherId` loads (the committed neutral file has `launcherId: null`, so
`npm start` works), but submitting stops with `MSG_LAUNCHER_ID` ("…launcherId is missing: run plugin/install.py…"); a real install
always writes the ID. `temporary.enabled`/`allow` carry `plugin_temporary_modes`.

New fields (the rest are unchanged): `"peopleMax": null | number`, `"partSize": number`,
`"temporary": {"enabled": bool, "allow": ["duration","endDate"], "units": ["HOURS",...], "maxDays": null | number}`.
`runtime-config.ts` validates: peopleMax null or ≥ 1; partSize 1..250; allow/units from the fixed lists.

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
with a clear error when it's missing. It runs each enabled deployment by calling the existing `launcher/*.py` and `plugin/*.py` `main(argv)` functions. They're loaded
by file path, because both folders have an `install.py`. Those scripts keep working on their own. `plugin/install.py` keeps its flags
and takes its `--public` default from `cfg.plugin_public`.

## 7. The plugin's Approvals tab: list, decide, confirm
Item approvers decide the per-person approvals a bulk request created, one INC at a time. Built in `bulk-api.service.ts`
(`pendingAccessApprovals`, `decideApprovals`, `approvalStatuses`), `approvals.ts` and `approvals-store.ts`; settings from §5
`approvals`. Facts verified live as a non-admin (2026-10-08) are marked ✔.
- **List:** `GET /v2025/generic-approvals?mine=true&include-comments=true&limit=250&offset=N&sorters=createdDate`
  `&filters=status eq "PENDING" and type eq "ACCESS_REQUEST_APPROVAL"` (URL-encoded), paged up to `maxRows`; "more" is shown
  when the cap is hit. `mine=true` keeps admins to their own (a non-admin only ever gets their own ✔).
- **Quirk ✔:** with `include-comments=true` rows carry `comments[]` but **no `assignedTo`**. Never rely on `assignedTo`.
- **Grouping:** by `parse_bulk_comment` / `parseBulkComment` (§2) over each row's `comments[]`; rows without a bulk comment
  form one *Other* group, shown only with `showOther`, and **view-only**. An action never spans INCs.
- **Default path (everyone):** `POST /v2025/generic-approvals/{id}/approve|reject`, body `{comment}` (or `{}` with no comment).
  Approve ✔ and reject ✔ both answer 200 with the approval (the spec says 204 for reject; accept either). `concurrency`
  (1..8) calls in flight, all generic-approvals calls of the tab spaced to **≤ 8 per second** (limit: 100 per 10 s per
  client and API version). 429 and 5xx are retried (4 attempts, growing back-off; the plugin SDK hides `Retry-After`).
  A non-admin deciding someone else's approval gets 403 ✔.
- **Bulk path:** `POST /v2025/generic-approvals/bulk-approve|bulk-reject` `{approvalIds (≤ 50), comment?}` → `202 {}` even for
  unknown IDs. Non-admins get **403 even for their own approvals ✔**, so it is used only when `useBulkEndpoint` is
  `always`, or `auto` and the user is ORG_ADMIN. A 401/403 switches that batch and the rest to the default path.
- **Deny** needs a non-empty comment when `denyCommentRequired`.
- **Confirm:** a 2xx or 202 proves nothing. Re-read with `GET /v2025/generic-approvals?limit=250&filters=approvalId in ("a",…)`
  (50 IDs per call, no `mine`), after 0, 1, 2, 4 and 8 s. The list has `status` but **empty `approvedBy`/`rejectedBy` ✔**;
  only `GET /v2025/generic-approvals/{id}` names the decider, so it is called only for IDs whose status changed although
  our call failed. Outcome per ID:
  | Outcome | When |
  |---|---|
  | `confirmed` (decided by me) | status = APPROVED/REJECTED as sent and our call succeeded, or the detail names the caller |
  | `elsewhere` (decided by someone else) | any other decided status, or the ID is no longer returned. A governance-group colleague or an admin decided first: **not an error** |
  | `pending` (still pending) | our call succeeded but the last re-read still says PENDING; can be retried |
  | `failed` | our call failed and it's still PENDING; can be retried, with the reason |
- After a run, `confirmed` and `elsewhere` rows leave the list; a later approval step of a serial scheme appears on refresh.

## 8. Where the catalog and the workflow calls come from
Verified live with read-only calls on the test tenant (2026-10-08); the test endpoint was checked in the spec only (it starts a run).
- **Workflows (plugin):** `GET /v2025/workflows?limit=250` (find by name), `POST /v2025/workflows/{id}/test` `{input}`
  (ORG_ADMIN, disabled workflow; same request and `{workflowExecutionId}` response as v3 in the spec), and
  `GET /v2025/workflow-executions/{id}` (`{id, workflowId, requestId, status, startTime, closeTime}`, the same keys as v3).
  The v3 paths send `Deprecation: Wed, 31 Mar 2027`; the v2025 ones send no deprecation header. The installers already use v2025.
- **Catalog, access profiles and roles:** `GET /v3/requestable-objects?identity-id=<signed-in user>&types=…&types=…&limit=250&offset=N`
  (a non-admin gets **403 without `identity-id`** and 200 with their own ✔ non-admin; the installer's PAT calls it without), with only the
  configured types among `ACCESS_PROFILE`/`ROLE` (`rules.requestable_object_types` / `requestableObjectTypes`); not called
  when none is configured (no `types` means every type). `ENTITLEMENT` is outside its `types` enum: alone → 400, next to
  another type → silently dropped (no entitlement rows although the tenant had 10 requestable ones).
- **Catalog, entitlements** (when `catalog.types` contains `ENTITLEMENT`): `GET /v2025/entitlements?filters=<f>&sorters=name&limit=250&offset=N`,
  where `<f>` = `rules.entitlement_filter(cfg)` / `entitlementFilter(cfg)`:
  `requestable eq true`, plus ` and name sw "<nameStartsWith>"` (quoted like every filter value: `"` and `\` escaped).
  Rows have `source.name` and `description` but **no `type`**: the caller adds `type: "ENTITLEMENT"` before `catalog_options`,
  so the item sent to the workflow stays `{id, type: "ENTITLEMENT", name}`. "Any" user level (`idn:entitlement:read`).
- **"Already has it" (plugin review step, best effort):**
  - access profiles and roles: `GET /v3/requestable-objects?identity-id=<id>&types=…&filters=id in (…)` → `requestStatus`
    `ASSIGNED` / `PENDING`. A non-admin may pass **another person's** `identity-id` and gets their status ✔ non-admin;
  - entitlements are checked **for ORG_ADMIN only** (`entitlementsChecked()`): for a non-admin identity search is 403 and another
    person's `access-request-status` is 400 "must be the current user" ✔ non-admin. The review step then says, in a muted line,
    that entitlements aren't checked;
  - entitlements, held: `POST /v3/search?limit=250` `{indices: ["identities"], query: {query: "id:(<id> OR …)"}, queryResultFilter:
    {includes: ["id","access.id","access.type"]}}`, 100 people per call; a chosen entitlement in `access[]` → `ASSIGNED`.
    Identities missing from the search index (it lags, and some identities aren't indexed) get no warning;
  - entitlements, pending: `GET /v3/access-request-status?requested-for=<id>&request-state=EXECUTING&limit=250`; a row with
    `type: ENTITLEMENT`, a chosen `id` (the row `id` is the item's ID) and not `requestType: REVOKE_ACCESS` → `PENDING`;
  - one entry per person and item; `ASSIGNED` wins.
- **People (search and pasted lists).** ORG_ADMIN: `/v2025/identities`, identity search and accounts as above (with fallbacks
  for identities not indexed yet). Everyone else (those three are **403** ✔ non-admin): `GET /v3/public-identities?limit=…&sorters=name&filters=…`
  ✔ non-admin. Type-ahead: `displayName sw v or alias sw v or email sw v or firstname sw v or lastname sw v`; pasted IDs: `id in (…)`
  (50 per call); pasted words: `alias eq v or email eq v`. Matching is case-insensitive. `name`/`status` filters and `co`/`in` (except
  on `id`) are 400; `limit` ≤ 250. Rows: `id, name, alias, email, attributes[{key,value}]` (department from `attributes`, no
  display name). No fallbacks: someone public identities doesn't list stays unresolved (paste their ID, or ask an admin).
- The Launcher form's `items` SELECT keeps `maximum: catalog.maxItems` (≤ 25, under the 30-selection limit); entitlements only
  add options, not selections.

## 9. Submitting through the Launcher (`plugin.submit: "launcher"`, the default)
The plugin drives the Launcher deployment as the **signed-in user**, so anyone holding the Launcher Access profile can submit:
no ORG_ADMIN, no plugin workflow, no backend. Verified live on 2026-10-08 as a real **non-admin** with Launcher Access, using
their own ISC UI session token (✔), and with the admin PAT (✔ PAT).

**Per part** (`bulk-api.service.ts` `launch`, `launcherFormInstance`, `submitLauncherForm`; `request-store.ts` `startViaLauncher`):
1. `POST /v2025/launchers/{launcherId}/launch` body `{}` → `200 {"interactiveProcessId": "<ULID>"}` ✔ (ISC's Launchpad calls
   `/beta/launchers/{id}/launch`; v2025 works the same). A non-admin **can't list** launchers (`GET /v2025/launchers` → 500
   "insufficient authorization") but can `GET /v2025/launchers/{id}` ✔, so `plugin/install.py` writes `launcherId` (§5).
2. `GET /beta/interactive-processes/{ipid}/blocks` → `{"items": [{"type": "FORM", "config": {"formInstanceId": "<uuid>"},
   "data": {"title", "message"}, "id", "created"}]}` ✔, polled every second until the FORM block appears, for at most 30 s
   (`LAUNCHER_FORM_TIMEOUT_MS`; then "…its form didn't appear within 30 seconds…"). `GET /beta/interactive-processes/{ipid}` ✔
   (owner = the user). **Beta**, with no v1/v2025 equivalent today; the admin PAT (client credentials) gets **401** on it ✔ PAT,
   so it only works with a user session. A non-admin can't list `/v2025/form-instances` (403) but can `GET /v2025/form-instances/{id}` ✔.
3. `PATCH /v2025/form-instances/{id}` (`application/json-patch+json`)
   `[{"op":"replace","path":"/formData","value":{…}},{"op":"replace","path":"/state","value":"SUBMITTED"}]` → 200 ✔; one PATCH
   went ASSIGNED → COMPLETED. Repeated (up to 3) until `SUBMITTED`/`COMPLETED` or `formErrors` (then the part is "not started"
   and shows the errors; the form stays open until it expires). The plugin SDK has no PATCH, so `SailpointPluginService.patch`
   calls `fetch` with the SDK's token and the tenant's `apiUrl.idn`. The form instance's `createdBy` `{type: WORKFLOW_EXECUTION, id}`
   is the run, which the approval references (`referenceData` `workflowExecutionId`).

**formData** (the Launchpad's own shapes, `LauncherFormData`): `people` (identity IDs; 40 and 250 accepted ✔: the 30 cap is the
UI picker's only), `items` (`{id,type,name}`; an item outside the form's STATIC options is accepted ✔), `approver` (one-item
list), `inc`, `justification`, `accessType` (boolean), `duration` (string), `durationUnit` (one-item list of the suffix, or `[]`),
`partLabel` (`""` or `" (k/n)"`). `rules.launcher_form_access` / `launcherFormAccess` map a validated `removeDuration`:
`""` → `false, "", []`; `"30d"` → `true, "30", ["d"]`; an end date → hours, `"720h"` → `true, "720", ["h"]`. A unit outside
the form's options (e.g. `"h"` when hours aren't offered on the Launchpad) is accepted by the form ✔ PAT; the Launcher workflow
accepts hours whenever the plugin offers an end date (`definitions.launcher_workflow_units`). Unknown formData keys are kept ✔ PAT.
The form's REGEX rules apply on submit, so the plugin validates first (rules.ts).

**The Launcher form and workflow** (`definitions.py`): a `HIDDEN` element `partLabel` (default `""`, no validations; the API
accepts `HIDDEN` ✔ PAT). The workflow defines `partLabel` as `"single"` emptied by a replace transform (in *Define Variable Access*),
then *Part Given?* (`StringMatches` `PART_LABEL_REGEX` on the form field) → *Set Part*. A missing field (Launchpad) or a
malformed one (`" (1/2) evil"`) leaves it empty ✔ PAT; `" (2/3)"` gives the approval `Bulk access INC… (2/3)` ✔ PAT. The access label
is the Launcher's own (`Temporary: 720h` for an end date, not `Temporary: until …`).

**Who submitted:** the generic approval's `requester` is the signed-in user ✔ (server-derived from the session: it can't be
spoofed). With the admin PAT, 250 people in one form reached the workflow intact ✔ PAT (an approver placed at #250 was caught by
*Approver In People?*).

**Following a part** (only what a non-admin can read; no `workflow-executions`): `GET /v2025/generic-approvals?limit=250&sorters=-createdDate&requesterId=<me>` (the **query parameter**: `filters=requesterId eq …` returns `[]` for a non-admin ✔ non-admin)
(filter and newest-first sort work ✔ PAT), matched by `workflowExecutionId` or by name
`Bulk access {inc}{partLabel}`; decided → done. Until the approval exists, the process's blocks are read again: a non-FORM block
with category `ERROR` (the workflow's *Reject …* interactive messages; shape assumed from the FORM block) → "The workflow stopped
before the approval: {title}: {message}". After 10 minutes, "still waiting".

**Errors:** a 401/403, or a 500 whose message says "insufficient authorization", on any of these calls →
`To submit, you need '{launcherAccessName}'. Request it in the Request Center.` (`errors.ts` `describeSubmitError`),
and the remaining parts are not tried.

## 10. Emails (`definitions.py`: `pending_email`, `approved_email`, `denied_email`, `rejected_email`)
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
- The generic approval's output has `approvedBy[]` / `rejectedBy[]` (`name` = whoever acted, e.g. an admin on the
  approver's behalf) and `comments[]`: the first is the one the workflow set, later ones are the decision's.

What the emails do with that:
- Because the body is Velocity (above), it is **constant markup** (no `{{…}}`, no `##`, no `#` outside directives, colours as `rgb()`), and every value travels in the
  context as **JSONPath** (`"key.$"`), never as a `{{…}}` template: a template value with a newline, quote or backslash
  fails the send. Config text goes in as plain context values. `core/tests/velocity_lite.py` renders a step the way
  SailPoint does; 8 real bodies (one-item lists, a hostile multi-line justification, missing paths) were checked
  byte-for-byte against SailPoint's rendering.
- **Layout** (all four): a coloured title bar, a DRY RUN banner in `dry-run`, then sections **What** (INC and part,
  each item as "name (access profile|role|entitlement)", "N people" / "1 person", how long), **Why** (justification,
  requested by), **Decision** (approver, decided by = `approvedBy[0].name` or `rejectedBy[0].name`, i.e. an admin acting
  for the approver too; "Nobody (it expired)"; the comments after the request's own first one, or "No comment"),
  **What happens next** / **What to do now**, and **Need help?** (`help_html(cfg)`: `helpContact` escaped, web and email
  addresses linked, default "Contact your SailPoint administrator."). One table, inline styles, max 640 px wide.
- **Steps and recipients** (requester; cc the bulk approver when `ccApprover`; everything to `overrideRecipients` when set):
  | Step | When | Subject | cc approver |
  |---|---|---|---|
  | `Email Pending` | before `Bulk Approval`, when `pending_email` (Launcher: after *Notify Pending*) | `Waiting for approval: bulk access {inc}{part}[ (DRY RUN)]` | yes |
  | `Email Approved` | status APPROVED | `Approved: bulk access {inc}{part}[ (DRY RUN: nothing was requested)]` | yes |
  | `Email Denied` | any other status (REJECTED, EXPIRED) | `Not approved: bulk access {inc}{part}` | yes |
  | `Reject …` (plugin) / `Email Reject …` (Launcher, after its Launchpad message) | a check failed before the approval | `Not sent for approval: {title} ({base_name})` (no INC: it may be the bad value) | no |
- **Rejections name the field:** Self approval and approver among the people → *Approver*; bad INC → *ServiceNow
  incident (INC) number* (shows the value entered); Launcher duration → *Duration* / *Unit* (shows the value entered);
  plugin `removeDuration` → *Temporary access (how long)*. The Launcher's ERROR message is now
  `<p>{field}: {problem}</p><p>Nothing was sent for approval. Fix it and submit again; you'll also get this by email.</p>`
  (the plugin shows it as "The workflow stopped before the approval: {title}: {message}", §9).
- **Links** (`definitions.EmailLinks(ui, plugin_id)`, built by the installers): Approvals → Other
  `/ui/d/approvals/other/requested-items`, Approvals → Access Requests `/ui/d/approvals/access-request/requested-items`,
  Launchpad `/ui/d/launchpad` (paths read from the live UI's navigation), the plugin `/ui/plugin/{pluginInstanceId}`
  (only with `deploy_plugin`). The plugin ID comes from `GET /ui-plugins/v1/resolve-alias?alias=<plugin.alias>` with
  `X-SailPoint-Experimental: true` (the CLI's endpoint; 200 with `pluginInstanceId`, 404 for an unknown alias; works with
  the PAT). The Launcher installs first, so its emails get plugin links from the `apply` after the plugin's first upload;
  without a UI address the emails name the pages without links. The plugin has no per-tab URL, so the emails name the tab.
- What each email says next: *Pending*: the approver decides in Approvals → Other (task *Grant: Bulk access {inc}{part}*),
  Approve acts at once, the timeout and its outcome, track it in *My bulk requests*. *Approved* (live): item approvers
  decide in Approvals → Access Requests (or the plugin's Approvals tab when `plugin_approvals_enabled`), the item requests
  are filed by the workflow so they're not in the requester's Request Center; temporary access ends by itself. *Approved*
  (dry-run): nothing was requested. *Not approved*: read the comment (or check with the approver when expired) and
  resubmit in the plugin (New request tab) or the Launchpad. *Not sent for approval*: fix the named field and resubmit.

## Test data and safety (live tests in a test or shared tenant)
- Only touch objects named with the config's prefix. Use a harmless test access profile as the only catalog item
  (`catalog.nameStartsWith`), test identities as the people, and an approver who isn't the PAT user (the requester).
- Each live grant uses a fresh test identity; the brief lists which ones.
- **Every approval a test creates must be decided** (approve or reject via the API) or cancelled. Never leave one pending.
- Leave every installation in `mode: dry-run` when you finish.
- No secrets in files or output. Never `sail --debug`. No `sed -i` (it breaks file permissions on this mount).
