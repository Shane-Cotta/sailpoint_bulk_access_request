# Bulk Access Request — developer guide

Bulk access requests for SailPoint Identity Security Cloud (ISC): many people × many Request Center items, tracked by a
**ServiceNow INC number**. There is no approval of the bulk request itself: **each item is approved the way SailPoint has it
set up** (owner, manager, source owner, none). Two independently installable deployments share one core and one config file.
Human docs: `README.md` (overview), `INSTALL.md` (any tenant), `USAGE.md` (requesters, approvers, admins), `plugin/README.md`.

## Layout
| Path | What |
|---|---|
| `bulkaccess.py` | The one CLI: `show-config`, `apply [--dry-run] [--only launcher\|plugin] [--deploy] [--grant me\|<ids>]`, `status`, `uninstall [--yes]`, `export [--offline] [--out]`; `apply --workdir` points `--deploy` at another Angular project. It calls each enabled deployment's `main(argv)`, loaded by file path (both folders have an `install.py`). |
| `core/bulkaccess/` | Shared Python (standard library only): `config.py` (the only config loader, plus derived per-route values), `tenant.py` (PAT client), `api.py` (every API path), `rules.py` (validation, parts, temporary access, the bulk item comment, which items need no approval), `catalog.py` (tenant reads: the catalog and each item's approval setup), `definitions.py` (pure JSON builders for the form, workflow and launcher) |
| `launcher/` | Deployment A: `install.py`, `status.py`, `uninstall.py`, `e2e.py` (still runnable on their own) |
| `plugin/` | Deployment B: Angular + PrimeNG UI plugin (it files the access requests itself, or drives the Launcher), plus `install.py` / `status.py` / `uninstall.py` / `pluginlib.py`. Its API paths are in `src/app/bulk/api-paths.ts` |
| `config/` | `bulk-access.example.json` (committed; the schema). Per-tenant `config/<tenant>.json` files are **gitignored**. |
| `docs/dev/CONTRACTS.md` | The shared spec: the central config, exact rule messages, the item comment, both submit modes, the Launcher workflow and emails, live-verified facts |
| `docs/screenshots/` | Images used by the docs |
| `tools/demo-capture/` | Playwright script for the plugin screenshots (`plugin-*.png`) and the demo video, from the plugin's demo mode (`npm run start:demo` in `plugin/`, then `npm run capture` in `tools/demo-capture/`). Output in `out/` (gitignored). |
| `.claude/agents/` | `bulk-access-builder.md`: the agent definition for parallel workstreams (one worktree and branch each); `dead-code-reviewer.md`: a read-only dead-code review, run before a merge |

## Conventions
- **One config.** Every setting for both deployments lives in `config/<tenant>.json`, grouped by concern, not by deployment.
  `core/bulkaccess/config.py` is the only loader. Route-specific values are **derived there** (`launcher_people_cap`,
  `plugin_people_max`, `launcher_temporary_modes`, `plugin_temporary_modes`, `plugin_submits_directly`,
  `plugin_submits_via_launcher`, …); never re-implement them in an installer. `apply` installs the Launcher before the plugin
  (launcher submit mode needs its ID). Renamed keys stay readable and add a note to `cfg.deprecations` (e.g.
  `launcher.accessApproval` → `access.launcherApproval`); removed ones (`approval`, `notifications.ccApprover`,
  `notifications.pendingEmail`, `plugin.submit: "test-endpoint"`) still load, are ignored, and add a note too.
- **API paths live in one module per language** (`core/bulkaccess/api.py`, `plugin/src/app/bulk/api-paths.ts`). Use GA
  `/<service>/v1` paths (legacy `v3`/`beta`/`v202X` lose support in Q2 2028). The only exceptions: `GET
  /beta/interactive-processes/{id}/blocks` (launcher submit mode; no GA path) and the experimental `/ui-plugins/v1`.
- **Generated files are never edited by hand:** `plugin/public/bulk-access.config.json`, `plugin/sp-ui-plugin.json`, and the
  workflows and form in the tenant all come from the config.
- **No tenant-specific anything in code.** Names come from the config `prefix`, IDs are looked up by name at install time.
  Example values use the placeholder prefix `ACME`.
- **New installs default to `mode: dry-run`**; `live` actually requests access. Test with test identities and a harmless
  test access profile, and set `notifications.overrideRecipients` in shared or test tenants.
- **Never commit credentials.** The PAT comes from `SAIL_BASE_URL` / `SAIL_CLIENT_ID` / `SAIL_CLIENT_SECRET` (env or the config's `envFile`).
- Python changes: keep `core` dependency-free and pure where possible; update `core/tests/test_core.py`.
- The plugin's `src/app/bulk/rules.ts` mirrors `core/bulkaccess/rules.py`; change both together (their specs mirror each other).
  Problem messages must match **exactly** in both languages (see `docs/dev/CONTRACTS.md` §2). So must the item comment:
  `<INC> | Bulk access request by <requester> | <access label> | <justification>` (the old 5-field form with
  `Approved by <approver>` is still read, so older approvals keep grouping).
- Plugin: `npx -y npm@11 install` (npm 11.12+), `npx ng test --watch=false`, `npm run build`. SailPoint CLI `sail` ≥ 2.7.0 for upload.
  **Never run `sail` with `--debug`** (it persists and prints tokens). `sail ui-plugins push-manifest` replaces the whole manifest,
  so the installer always passes `--private` unless `--public` or `plugin.public` asks otherwise.
- Tests: `python -m pytest -q -p no:cacheprovider core/tests plugin/tests` (no tenant needed).

## ISC behaviour this design depends on (all verified against a live tenant)
- **Forms:**
  - REGEX validation is `{"validationType":"REGEX","config":{"regex":…,"message":…}}`.
  - **Never** put a MAX_LENGTH rule on a TEXTAREA: the submission then never reaches the workflow.
  - A SELECT allows at most 30 selections.
  - STATIC select options may carry full `{id,type,name}` objects. INTERNAL selects ignore queries; SEARCH selects return names, not IDs.
  - The INTERNAL identity picker only matches complete usernames, and doesn't list uncorrelated identities.
  - Through the API, a form may need two PATCHes to go ASSIGNED → IN_PROGRESS → SUBMITTED. The API enforces REGEX rules on
    submit: a failing value leaves the instance IN_PROGRESS with `formErrors`, and the workflow waits forever (cancel it).
  - formData shapes: a TOGGLE arrives as a boolean, TEXT as a string, and a one-choice SELECT as a one-item list (steps accept it like a string).
- **Generic approvals (the plugin's Approvals tab):**
  - `/generic-approvals/v1` also returns access-request approvals, as `type: ACCESS_REQUEST_APPROVAL` (with `requestee`,
    `requestedTarget`, `assignedTo`, `approvalConfig.serialChain`, `referenceData`). Their `comments[]` (where our INC is) only
    come back with `include-comments=true`, and `assignedTo` is then left out. Comment text can't be searched or filtered, so group by INC in the page.
  - `mine=true` returns only the caller's approvals. For a non-admin the plain list is already theirs, and `approverId=<someone else>` returns 400.
  - `bulk-approve` and `bulk-reject` take at most 50 IDs and return `202 {}` even for unknown IDs, so re-read
    to confirm. **A non-admin gets 403**, even for their own approvals.
  - **Deciding as a non-admin:**
    - Single `/{id}/approve|reject` works on the caller's own approvals and is recorded as theirs (`approvedBy`/`rejectedBy`,
      with `actionedAs`, e.g. `ACCESS_PROFILE_OWNER`).
    - On someone else's approval it returns 403.
    - Reject returns 200 with the approval (the spec says 204).
  - An ORG_ADMIN can approve or reject on someone's behalf. That's recorded as a manual reassignment: the original approver is the first `reassignmentHistory[].reassignedFrom`.
  - The list leaves `approvedBy`/`rejectedBy` empty; only the single GET has them.
- **Requesting access directly (`POST /access-requests/v1`, the plugin's default; verified 2026-10-13 as a non-admin):**
  - Works for other people when the tenant allows it (`GET /access-request-config/v1` →
    `requestOnBehalfOfConfig.allowRequestOnBehalfOfAnyoneByAnyone`; the plugin installer checks and falls back to the Launcher).
  - At most **10** `requestedFor` per request; items of all three types; each item carries its own `comment` and `removeDate`
    (an entitlement too). A 4000-character comment is accepted.
  - Response: `newRequests[]`, one per person and item (`{requestedFor, requestedItemsDetails[{id,type}], accessRequestIds[]}`),
    and `existingRequests[]` for ones already open.
  - The **requester is the signed-in user**. If they are also an item's approver, the approval is reassigned to an admin.
  - A person who **already has a pending request for the item** still shows in `newRequests`, but SailPoint cancels it at
    once (`state: CANCELLED`, `cancelledRequestDetails.comment` "Already has a pending request for this item", by System).
    The plugin warns beforehand (requestable-objects `requestStatus: PENDING`) and shows the reason on My bulk requests.
  - Phases run SOD_PHASE → ACCESS_REQUEST_POLICY_CHECK_PHASE → APPROVAL_PHASE → PROVISIONING_PHASE; the SOD phase
    took 2–10 minutes in the demo tenant, and `approvalDetails` stays empty until the approval phase starts.
  - Cancel: `POST /access-requests/v1/cancel` `{accountActivityId, comment}`, where `accountActivityId` is the status row's
    `accessRequestId`; the requester can cancel while it is open.
- **Item approval schemes** (verified 2026-10-13):
  - An access profile or role with `accessRequestConfig.approvalSchemes: []` is granted **without any approval** (the approval
    phase completes at once). An entitlement with none of its own falls back to the tenant default
    `entitlementRequestConfig.grantRequestApprovalSchemes` (e.g. `sourceOwner`). The installers list the no-approval items
    (`catalog.no_approval_ids`) and the plugin warns about them.
  - Schemes are read with `/access-profiles/v1` and `/roles/v1` (`filters=id in (…)`), and per entitlement
    `GET /entitlements/v1/{id}/entitlement-request-config`. A non-admin can't GET an access profile (403).
- **Requesting access from a workflow (the Launcher):**
  - `sp:create-approval-request` breaks on one-item lists (the engine unwraps single-element arrays); use `sp:access:manage`.
  - A request takes at most 10 recipients, and nested loops are rejected, so the workflow loops over people and each request carries all the items.
  - Steps inside a loop only see `$.loop.*`, so the loop gets `context.$: "$"` and reads `$.loop.context.…`.
  - Item approval schemes apply to the requests the workflow submits: an access profile with an OWNER scheme gets one
    approval per person for its owner.
  - The **requester** of those requests is always the **workflow owner** (the PAT user), not whoever filled in the form. If the
    workflow owner is also an item's approver, ISC escalates that approval to an admin ("…because the Identity X is the Requester").
  - **Loop (`sp:loop:iterator`) has a hard 250-item limit:** above it the step fails ("Input has N iterations which exceed 250
    iteration limit"). 250 runs in about 13 s, in parallel. So one workflow run covers at most 250 people; the plugin sends parts.
  - **Serial Loop (`sp:serial:iterator`) is not usable:** it silently stops after 50 items with no error (and at the first failing item).
- **Temporary access (Manage Access `versionNumber: 2`):**
  - v2 has `removeDuration` (and `startDate`). v1 only had days and weeks, and not for entitlements. Temporary entitlements work in v2.
  - `removeDuration` is a string `"<n><suffix>"`: `"2h"`, `"1d"`, `"1w"` (+7 days), `"1M"` (+1 month).
  - Missing (a JSONPath to a missing key) or `""` = permanent (`removeDate: null`), so always pass it; no branching needed.
  - An invalid value (e.g. `"abc"`) **fails the step** ("timeext: invalid duration"). Only send validated strings.
  - No workflow transform turns a date into a duration, so the Launcher offers durations only; the plugin converts an end date to hours itself.
- **`/access-request-status/v1` rows:** `requestedFor` is an object `{id,name,type}`, `name` is the item name, `removeDate` holds the
  expiry, `requesterComment.comment` holds our comment, `accessRequestPhases[]` the phases, and `approvalDetails[]`
  (`{status, currentOwner{id,name,email}, originalOwner}`; empty until the approval phase starts) who it waits for.
  There's no `requestedObject`. `requested-by=<me>` (with `sorters=-created`) lists the caller's own requests.
- **Emails (`sp:send-email` v2):**
  - The body is a **Velocity** template, rendered with the step's `context` map. Text that isn't valid Velocity
    (e.g. a justification containing `#if(`) templated into the body makes the send 400 and **fails the run**.
  - So bodies are fixed markup, and every value comes in through `context` as JSONPath (`"key.$"`), which arrives intact.
    A `{{…}}` template value is spliced in raw: a newline, `"` or `\` in it fails the send too.
  - One-item lists arrive unwrapped in the context. `{{$.list[*].name}}` renders Go-style (`["a" "b"]`), so lists go
    through the context and `#foreach`.
  - The execution history shows the context, not the rendered mail; `core/tests/velocity_lite.py` renders a step locally
    (CONTRACTS §10 has the live probe technique).
- **Workflow definitions:** failure end steps need top-level `failureName` / `description`. Launcher-triggered workflows must filter
  `$[?(@.workflowId == '<own id>')]`. Run history: `GET /workflow-executions/v1/{id}/history-v2` (`{definition, trigger,
  history}`); `/workflow-executions/v1` has no list (404), and the old `GET /workflows/{id}/executions` is deprecated.
- **Variables and operators:**
  - "Update Variable" only accepts variables from a step whose name starts with "Define Variable".
  - A variable can't be defined as a literal `""`, but a replace transform that empties a placeholder works. Concatenation fails on numbers.
  - `sp:compare-boolean` treats the string `"true"` as false.
- **Angular/PrimeNG:** the stepper (`p-step-panel` content templates) creates every step's component when the page
  opens, so a step must react to `store.step()` (an effect), not `ngOnInit`, to do work when it's shown.
- **Launchers:**
  - Visible and launchable only for holders of the auto-created `assignedLaunchers` entitlement (on the IdentityNow source). The installer wraps it in a requestable "Launcher Access" profile.
  - **Replacing an access profile's `/entitlements`** (even with the same list) leaves its holders' requestable-objects
    `requestStatus` at `AVAILABLE` until their next identity refresh (seen live 2026-10-13: still wrong after 7 minutes;
    `POST /identities/v1/process` with `X-SailPoint-Experimental: true` fixed it in 30 s). A description-only PATCH
    doesn't. The plugin's submit check reads that status, so the installer PATCHes only what differs
    (`definitions.access_profile_changes`).
  - Disabling the workflow disables its Launcher a moment later.
- **Plugins:** a browser plugin can't hold a workflow's external-trigger secret. Two ways to submit (`plugin.submit`,
  `docs/dev/CONTRACTS.md` §3 and §9); either way only holders of the Launcher Access profile may submit, checked with
  `GET /requestable-objects/v1?identity-id=<me>&types=ACCESS_PROFILE&filters=id eq "<profile id>"` → `requestStatus: ASSIGNED`
  (works for a non-admin; the installer writes `launcherAccessId` into the runtime config):
  - **`direct`** (default): the plugin calls `POST /access-requests/v1` as the signed-in user, 10 people per request.
  - **`launcher`** (fallback when the tenant doesn't allow requesting for others): the plugin drives the Launcher **as the
    signed-in user** (verified as a real non-admin): `POST /launchers/v1/{id}/launch` `{}` → `{interactiveProcessId}`; poll
    `GET /beta/interactive-processes/{ipid}/blocks` until the `FORM` block (`config.formInstanceId`); `PATCH
    /form-instances/v1/{id}` (JSON Patch: `/formData`, then `/state` `SUBMITTED`; one PATCH went ASSIGNED → COMPLETED);
    then the blocks again for the workflow's INFO (*Notify Requested*) or ERROR (*Reject …*) message.
  - A non-admin **can't list** launchers (500 "insufficient authorization") or form instances (403), but can GET one by ID;
    so the installer writes `launcherId` into the runtime config. The admin PAT (client credentials) gets 401 on
    `/beta/interactive-processes` (and on `/interactive-processes/v1`): it needs a user session.
  - Through the API the form takes what its pickers don't: 250 people, items and SELECT values outside its STATIC options, and
    unknown formData keys. Its REGEX rules still apply. A `HIDDEN` element is accepted; the Launchpad leaves it out of formData,
    and `StringMatches` on that missing path simply takes the default branch.
  - The SDK only has `get`/`post`, so `SailpointPluginService.patch` calls `fetch` with the SDK's token and `tenant.apiUrl.idn`.
- **APIs:**
  - Every `/v1` path in `api.py` / `api-paths.ts` answers without `X-SailPoint-Experimental`, with the same filters and shapes
    as the `v3`/`v2025` ones it replaced (accounts filters and search results compared; verified 2026-10-13 with the PAT).
  - `/requestable-objects/v1` only lists **access profiles and roles** (its `types` enum). `types=ENTITLEMENT` alone returns 400,
    and next to another type (repeated `types=`) it is silently dropped. Never call it without `types` (that means every type).
  - **Requestable entitlements** come from `GET /entitlements/v1?filters=requestable eq true[ and name sw "<prefix>"]`
    (`limit`/`offset` paging and `sorters=name` work). Rows carry `source.name` but **no `type`**: tag them `ENTITLEMENT`.
  - "Already has it" for entitlements: held = `POST /search/v1` on `identities` with `id:(…)`, reading `access[]`
    (`{id,type}`); identities missing from the search index get no warning. Pending = `GET /access-request-status/v1?requested-for=<id>&request-state=EXECUTING`
    (the row `id` is the item's ID).
  - `/form-definitions/v1` lists as `{results, count}`, `/launchers/v1` as `{items}`.
  - Some tenants sit behind Cloudflare, which rejects Python's default User-Agent; the client sends its own.
- **What a non-admin's own ISC session may call** (verified 2026-10-08 with two `sp:user` test users on the `v3`/`v2025`
  paths these replaced; ✔ = re-checked on `/v1` as a non-admin on 2026-10-13. The plugin branches on `capabilities.isOrgAdmin`):

  | Call | Non-admin | The plugin |
  |---|---|---|
  | `GET /requestable-objects/v1?types=…` without `identity-id` | **403** | always sends `identity-id=<me>` (200) |
  | `GET /requestable-objects/v1?identity-id=<me>&filters=id eq "<Launcher Access profile>"` ✔ | 200, `requestStatus: ASSIGNED` for a holder | who may submit |
  | `GET /requestable-objects/v1?identity-id=<someone else>&…&filters=id in (…)` | 200, with that person's `requestStatus` | "already has it" for access profiles and roles |
  | `GET /entitlements/v1?filters=requestable eq true` | 200 | catalog entitlements |
  | `GET /identities/v1` (list and `/{other}`), `POST /search/v1`, `GET /accounts/v1`, `GET /access-profiles/v1/{id}` ✔ | **403** | admins only |
  | `GET /public-identities/v1` | 200 | people search and pasted lists for non-admins |
  | `POST /access-requests/v1` for other people ✔ | 202 (when the tenant allows requesting for anyone) | submit (direct mode) |
  | `GET /access-request-status/v1?requested-by=<me>` ✔ | 200, their own requests with `approvalDetails` | My bulk requests |
  | `GET /access-request-status/v1?requested-for=<someone else>` | **400** "must be the current user" | entitlement "already has it": admins only |
  | `GET /generic-approvals/v1?mine=true&include-comments=true`, `POST …/{id}/approve\|reject` | 200, their own | Approvals tab |
  | `POST /launchers/v1/{id}/launch`, `GET /beta/interactive-processes/{id}/blocks`, `GET`/`PATCH /form-instances/v1/{id}` | 200 (with Launcher Access) | submit (launcher mode) |

  `/public-identities/v1` rows: `id, name, alias, email, status, identityState, manager, attributes[{key,name,value}]` (no
  display name; `department` is in `attributes`). Filters: `id` eq/in (50 IDs fine); `alias`, `email`, `firstname`,
  `lastname`, `displayName` eq/sw, case-insensitive, combinable with `or`/`and`; `in` only on `id`; `name`, `status` and `co`
  → 400. `sorters=name` (`alias` → 400), `limit` ≤ 250. No `/{id}` (404). It lists only what the tenant's public identity
  config shows: unlike the admin path there's no accounts fallback for identities not listed.
