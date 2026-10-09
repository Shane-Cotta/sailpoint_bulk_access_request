# Bulk Access Request: UI plugin (deployment B)

This is a page inside SailPoint Identity Security Cloud (ISC) for requesting access for many people at once:

1. **People.** Search for people, or paste a list of identity IDs, usernames or email addresses. There's no limit:
   paste 1,000 or more and the page looks them up in batches.
2. **Access.** Pick one or more items from the Request Center catalog.
3. **Approver and INC.** Choose one approver for the whole request (it can't be you, or anyone on the request), enter the ServiceNow
   incident number (INC) and a justification, and say how long the access should last: permanently, for a duration,
   or until a date.
4. **Review and submit.** The page then follows the approval and shows "Waiting for *approver*", followed by the outcome.

The approver decides the request. When they approve, SailPoint files one access request per person, each
with all the chosen items, and puts the INC number in every item's comment. A second tab, **My bulk requests**,
lists your bulk requests grouped by INC. A third tab, **Approvals**, is for the *item* approvers (owners, managers)
those requests then reach: it groups their pending approvals by INC and decides a whole bulk request at once
(see [Approvals tab](#approvals-tab-item-approvers)).

## Limits: no people limit, parts of 250

- **People:** no limit by default (`people.max: null`). Set `people.max` in the config if you want one.
- **Parts:** SailPoint's workflow loop refuses more than **250** items, so one workflow run, and so one approval, covers at
  most 250 people (`people.partSize`, 1 to 250). A bigger request is sent as several approvals, started one after another:
  600 people become `Bulk access INC0012345 (1/3)`, `(2/3)` and `(3/3)`. **Every part carries the same INC**, approver, items,
  justification and access choice. The approver decides each part, and only approved parts are requested. The page says
  this on the People step ("600 people → sent as 3 approvals of up to 250, same INC") and lists the parts on the review step.
- **If some parts fail to start**, the submitted view says which ones, and **Retry** starts just those again.
- **Items:** at most 25 per request (SailPoint's limit), `catalog.maxItems`.
- With more than 100 people, the review step skips the "who already has this" check (it costs one call per person).
  SailPoint skips what people already have anyway.

## Temporary access

On the Approver and INC step, *How long should the access last?* offers:

| Choice | Sent to the workflow (`removeDuration`) | Label |
|---|---|---|
| Permanent | `""` | `Permanent` |
| For a duration: a whole number and a unit (hours, days, weeks, months) | e.g. `30d`, `2h`, `1w`, `3M` | `Temporary: 30 days` |
| Until a date (after today) | the hours from now to 23:59:59 that day, your local time, e.g. `734h` | `Temporary: until 2026-11-07` |

SailPoint (Manage Access v2) removes the access automatically when the time is up. The duration counts from when the access
is granted, so if the approver takes longer, an end date moves later by the same amount; the page says so. The config's
`temporaryAccess` settings decide what is offered: `enabled`, `allow` (`duration`, `endDate`), `units` and `maxDays` (a cap;
`null` = none). When `enabled` is false the section is hidden. The checks and their messages are the same as the core's
(`rules.ts` mirrors `rules.py`). My bulk requests shows **Temporary until …** on every access request that has an end date.
The Launcher offers durations only (a workflow can't turn a form date into a duration).

## Approvals tab (item approvers)

Approving a bulk request doesn't end the approvals. Each access request the workflow files still goes through the item's
own approval scheme (owner, manager, source owner, governance group), so 300 people × 1 owner-approved item means 300
approvals for that owner. SailPoint's own Approvals page shows them one card at a time. The **Approvals** tab lists the
signed-in user's pending access-request approvals **grouped by the INC** in their comment, and decides them together:

- **Group cards** show the INC, the number of approvals, people and items, the access (`Permanent` or `Temporary: …`), who
  asked, who approved the bulk request, the oldest created date and the earliest due date.
- **Open a card** to see every approval (person, item, temporary end date, created, due) and the scheme you approve as.
  Untick any you don't want to decide; they stay pending.
- **Approve N / Deny N** with one comment (required to deny unless `approvals.denyCommentRequired` is false). A confirm
  dialog repeats the INC and the counts. An action never spans INCs.
- **Progress and results.** Decisions go out about 8 a second (SailPoint allows 100 per 10 s), `approvals.concurrency` at a
  time; throttled (429) and failed (5xx) calls are retried. Then the page re-reads every approval and reports each one as
  **approved/denied by you**, **decided by someone else** (a governance-group colleague or an admin got there first: not an
  error), **still pending** or **failed** (with the reason). While sending, the panel counts calls sent; "approved by you"
  only counts re-read, confirmed decisions. The result heading says **Approved** / **Denied** only when every approval is
  confirmed as yours; otherwise **Decided** (the rest by someone else), **Partly done** or **Not done**.
  **Retry** sends the failed and still-pending ones again.
- **Your own decision.** Each approval is decided through `POST /v2025/generic-approvals/{id}/approve|reject` as the signed-in
  user, so SailPoint records them as the approver (`approvedBy`, and *reviewed by* on the access request), exactly as if they
  had used the Approvals page. SailPoint refuses (403) a non-admin's decision on someone else's approval.
- **The bulk endpoint** (`generic-approvals/bulk-approve|bulk-reject`, 50 per call) refuses non-admins with 403, even for their
  own approvals (verified live). `approvals.useBulkEndpoint`: `auto` (default) uses it for ORG_ADMIN only, `always` tries it
  for everyone, `never` turns it off. A 403 switches back to one call per approval. A `202` proves nothing, so these are
  re-read too.
- **Only your own.** The list asks for `mine=true`, so an ORG_ADMIN also sees only approvals assigned to them.
- **Other approvals** (not from a bulk request) are listed in an *Other* group when `approvals.showOther` is true. They are
  view-only here; decide them one by one in SailPoint.
- **Large lists.** Up to `approvals.maxRows` (default 5,000) are loaded, oldest first; the page says when there are more.
- Approving a step of a multi-step scheme may create the next step's approval. If it's yours, it appears after **Refresh**.

**Who sees it.** The tab appears when `approvals.enabled` is true (the default) and the runtime config comes from an install
that knows about it. In test-endpoint mode, people without ORG_ADMIN open on this tab, and the ORG_ADMIN banner only shows on
*New request*. But an
approver can only open the plugin if they can see it: make it public (`plugin.public: true`), or keep it private and add the
approvers to the plugin's `restrictToUsers`. `show-config` warns when the tab is on and the plugin is private.

The same code installs into any tenant. Everything tenant-specific (names, INC rule, limits, catalog filter) comes
from a config file, and nothing is hard-coded.

## Screenshots

### In ISC
Real screens from a test tenant (prefix `UCSF`), submitted by a non-admin requester and decided by a non-admin approver.
The whole lifecycle, with the ISC screens around the plugin, is in [USAGE.md](../USAGE.md) and
[docs/sailpoint/END_TO_END_WALKTHROUGH.md](../docs/sailpoint/END_TO_END_WALKTHROUGH.md).

| | |
|---|---|
| ![People: one found by search, one by a pasted email](../docs/screenshots/isc-10-plugin-people.png) | ![Access: an access profile from the catalog](../docs/screenshots/isc-11-plugin-access.png) |
| ![Approver, INC, justification, 1 day](../docs/screenshots/isc-12-plugin-approver-inc.png) | ![Review: submitted through the Launcher as you](../docs/screenshots/isc-13-plugin-review.png) |
| ![Waiting for the approver](../docs/screenshots/isc-14-plugin-waiting.png) | ![My bulk requests, filtered by INC: approved](../docs/screenshots/isc-50-plugin-my-bulk-requests-approved.png) |
| ![Approvals tab: one INC waiting](../docs/screenshots/isc-40-plugin-approvals.png) | ![Approvals tab: approved, confirmed](../docs/screenshots/isc-43-plugin-approvals-result.png) |

### With demo data
Large requests, parts and partial results, made with the plugin's demo mode:

| | |
|---|---|
| ![People step: 600 people, sent as 3 approvals](../docs/screenshots/plugin-1-people.png) | ![Access step](../docs/screenshots/plugin-2-items.png) |
| ![Approver and INC](../docs/screenshots/plugin-3-approver-inc.png) | ![Validation](../docs/screenshots/plugin-3b-inc-validation-error.png) |
| ![Temporary access](../docs/screenshots/plugin-7-temporary-access.png) | ![Review of a request sent in 3 parts](../docs/screenshots/plugin-8-parts-review.png) |
| ![Review](../docs/screenshots/plugin-4-review.png) | ![Waiting for the approver](../docs/screenshots/plugin-5-submitted-waiting.png) |
| ![My bulk requests: parts and temporary access](../docs/screenshots/plugin-6-my-bulk-requests.png) | ![Approvals tab: pending approvals grouped by INC](../docs/screenshots/plugin-9-approvals-groups.png) |
| ![Approvals tab: one INC opened, one approval left out](../docs/screenshots/plugin-10-approvals-drilldown.png) | ![Approvals tab: confirm dialog](../docs/screenshots/plugin-11-approvals-confirm.png) |
| ![Approvals tab: decisions going out](../docs/screenshots/plugin-12a-approvals-progress.png) | ![Approvals tab: all confirmed](../docs/screenshots/plugin-12-approvals-progress-result.png) |
| ![Approvals tab: partly done, with Retry](../docs/screenshots/plugin-13-approvals-partial-retry.png) | *(Screenshots use made-up demo data. To retake them, see [tools/demo-capture](../tools/demo-capture/README.md).)* |

## Who can use it: Launcher Access to submit, any approver for the Approvals tab

| Who | Needs | Can use |
|---|---|---|
| **Requester** (any user) | The *"<prefix> Bulk Access Request - Launcher Access"* profile, requested in the Request Center (auto-approved with `access.launcherApproval: "NONE"`, else by their manager). It also puts the Launcher in their Launchpad | *New request*, *My bulk requests* (the approval's status; listing the item requests themselves needs admin rights), *Approvals* |
| **Bulk approver** | Nothing extra | Decides in ISC **Approvals → Other** ("Grant: Bulk access INC…"); the plugin isn't needed |
| **Item approver** (owner, manager, governance group) | Nothing extra, beyond seeing the plugin | *Approvals* tab, or ISC **Approvals → Access Requests** |
| **ORG_ADMIN** | — | Everything, plus the item requests in *My bulk requests* when they are the workflow owner |

Everyone must be able to see the plugin: make it public (`plugin.public: true`), or add them to its `restrictToUsers`.

How the page submits is set by `plugin.submit` in the config (`show-config` says which applies):

**`"launcher"` (the default when the Launcher deployment is installed): anyone holding the *Launcher Access* profile.**
The page drives the Launcher deployment (`../launcher/`) for the signed-in user, with their own ISC session: no ORG_ADMIN,
no plugin workflow, no backend. For each part it

1. starts the Launcher: `POST /v2025/launchers/{id}/launch` (the installer writes the Launcher's ID into the runtime config,
   because people who aren't admins can't list launchers);
2. waits for its form: `GET /beta/interactive-processes/{id}/blocks` until the FORM block names the form instance (up to 30 s);
3. fills in and submits that form: `PATCH /v2025/form-instances/{id}` with the people, items, approver, INC, justification,
   access fields and the hidden part label (`" (2/3)"`). The page checks everything first with the same rules as the form.

The Launcher's workflow then runs exactly as from the Launchpad, and the approval's requester is the signed-in user (SailPoint
takes it from the session, so it can't be faked). The 30-person and catalog limits of the Launchpad's form are its pickers'
only: through the API a part carries up to 250 people (verified live). An end date is sent as a number of hours in the form's
duration fields, so the approver sees `Temporary: 720h` rather than `Temporary: until …`. Following a part uses only what the
user may read: their generic approvals (`?requesterId=<me>`) and the process's messages; no workflow executions.

Someone without the profile gets **"To submit, you need '*<prefix> Bulk Access Request - Launcher Access*'. Request
it in the Request Center."** (SailPoint answers the launch with 401/403, or 500 "insufficient authorization").
This was verified live on 2026-10-08 as a real non-admin user with Launcher Access (see `docs/dev/CONTRACTS.md` §9).

**`"test-endpoint"` (the default without the Launcher deployment): ORG_ADMIN only.**

- A browser plugin can't safely hold the OAuth client secret that a workflow's external trigger needs.
- So the page starts its own workflow through SailPoint's **workflow test endpoint** (`POST /v2025/workflows/{id}/test`), using
  the signed-in user's own session. Only users with the right to test workflows (in practice, ORG_ADMIN) can call that endpoint.
- The test endpoint only runs **disabled** workflows. The installer creates the plugin's workflow disabled, so leave it
  that way.
- The *New request* tab shows a banner that explains this. For people who aren't ORG_ADMIN, it also says so, turns the Submit
  button off, and opens the Approvals tab first. Everyone else uses the Launcher from the Launchpad.

Either way, people can only open the plugin if they can see it: make it public (`plugin.public: true`), or keep it private
and add them to the plugin's `restrictToUsers`.

## What gets installed

All names come from `prefix` in the config (here `ACME`), so several copies can live in one tenant and you can always
tell them apart:

| Object | Name | Notes |
|---|---|---|
| Workflow (only with `plugin.submit: "test-endpoint"`) | `<prefix> Bulk Access Request (Plugin)` | **Disabled**, external trigger, owned by the installer's identity (or `owner`). In launcher mode it isn't created; `uninstall` still removes one left from an earlier install. |
| UI plugin | alias `plugin.alias` (default `<prefix>-bulk-access`), name `plugin.displayName` | Created **private** (only you can see it) unless `plugin.public` is true or you pass `--public`. |

In launcher mode the page uses the Launcher's workflow and form instead (see [INSTALL.md](../INSTALL.md)). The plugin
workflow: looks up the requester and approver → refuses self-approval and a bad INC (as a second check after the page) →
**one generic approval** named `Bulk access <INC>` (plus ` (k/n)` when the request is split into parts), assigned to the approver →
if approved and `mode` is `live`, **Manage Access once per person** with all items, the chosen `removeDuration`, and the comment
`<INC> | Bulk access request by … | Approved by … | <access> | <justification>` → an email to the requester (or to
`notifications.overrideRecipients`). The page starts one run per part with this input (every field always present):
`people` (≤ 250 identity IDs), `items`, `approverId`, `requesterId`, `inc`, `justification`, `part`, `parts`, `partLabel`,
`removeDuration`, `accessLabel`.

In `dry-run` mode everything runs, including the approval and the emails, except the access requests themselves. Start in dry-run.

## Requirements

- An ISC tenant with **UI Plugins** turned on, and a **personal access token** of an ORG_ADMIN, who becomes the owner of what's installed.
- Python 3.10+ (no extra packages), Node.js 22+ with **npm 11.12+** (`npx -y npm@11 …`), and the
  [SailPoint CLI](https://github.com/sailpoint-oss/sailpoint-cli) `sail` 2.7+.

## Configure

1. Copy `config/bulk-access.example.json` to `config/<tenant>.json` (files in `config/` are gitignored). The settings the plugin uses:

   | Setting | Meaning |
   |---|---|
   | `envFile` | A `.env` file with `SAIL_BASE_URL` (e.g. `https://acme.api.identitynow.com`), `SAIL_CLIENT_ID`, `SAIL_CLIENT_SECRET`. The secret never goes in the config. |
   | `prefix` | Names every object (see above). |
   | `mode` | `dry-run` (approve, but request nothing) or `live`. |
   | `inc.pattern`, `inc.example`, `inc.message` | The INC rule, e.g. `^INC\d{7}$`. Write a pattern that means the same in Python and JavaScript (plain classes such as `\d` and `[A-Z]`, anchors, groups). |
   | `catalog.types`, `catalog.nameStartsWith`, `catalog.maxItems` | Which Request Center items the page offers (max 25 per request). |
   | `people.max`, `people.partSize` | People per request (`null` = no limit) and people per approval (1 to 250, default 250). See *Limits*. |
   | `temporaryAccess.*` | `enabled`, `allow` (`duration`, `endDate`), `units`, `maxDays`. See *Temporary access*. |
   | `approval.*` | Timeout days, what happens at timeout, priority. |
   | `approvals.*` | The Approvals tab: `enabled` (default true), `concurrency` (1 to 8, default 4), `useBulkEndpoint` (`auto`, `always`, `never`), `maxRows` (250 to 20,000, default 5,000), `showOther` (default false), `denyCommentRequired` (default true). |
   | `notifications.overrideRecipients` | For test tenants: send every email here instead of to real people. |
   | `plugin.alias`, `plugin.displayName`, `plugin.public` | The plugin's alias (lowercase, digits, dashes), the name shown in ISC, and whether everyone can see it (default false: only you). |
   | `plugin.submit` | `"launcher"` (default with the Launcher deployment): submit through the Launcher as the signed-in user. `"test-endpoint"` (default without it): through the plugin workflow, ORG_ADMIN only. See *Who can use it*. |

2. The installer turns this into two files the page reads. Don't edit them by hand:
   - `public/bulk-access.config.json`: the runtime config (`submit`, the Launcher's ID and access profile name, or the workflow
     name and ID, INC rule, `peopleMax`, `partSize`, `itemsMax`, catalog filter, `temporary`). The committed copy holds neutral
     defaults (and no Launcher ID, so a build that skipped `install.py` refuses to submit). One without `submit` comes from an
     older install and uses the test endpoint. A runtime config without a `temporary` block
     (from an older install) turns temporary access off, because an older workflow would grant the access permanently.
     One without an `approvals` block hides the Approvals tab.
   - `sp-ui-plugin.json`: the plugin manifest (alias, name, `apiScopes: ["sp:scopes:all"]`, slot `full-page`).

## Install

Run these from `bulk-access-request/`:

```bash
# 0. Once: install the page's dependencies (needs npm 11.12+)
(cd plugin && npx -y npm@11 install)

# 1. See exactly what would be sent. This changes nothing.
python plugin/install.py --config config/<tenant>.json --dry-run

# 2. Look up the Launcher (launcher mode; install the Launcher deployment first) or create or update the
#    workflow (test-endpoint mode), write the runtime config and manifest,
#    build the page and upload it (first time: `sail ui-plugins create --private`)
python plugin/install.py --config config/<tenant>.json --deploy

# 3. Check
python plugin/status.py --config config/<tenant>.json --plugin
```

`install.py` is idempotent: it finds the workflow and the plugin by name and alias, and updates them. Re-run it after any config change.
Options:
- `--deploy` also runs `npm run build`, then `sail ui-plugins create` (first time) or `push-manifest` (after that), then
  `sail ui-plugins upload`. The plugin stays **private to you** unless the config says `"plugin": {"public": true}` or you pass
  `--public` (`--private` overrides the config). `push-manifest` replaces the whole manifest, visibility included, so the
  installer applies the same choice every time.
- `--workdir <folder>` builds a different copy of this folder (one with its own `node_modules`). The generated files are written there,
  not here. `sail ui-plugins create` also writes the tenant's CSP into that copy's `angular.json`.
- The CLI gets the PAT from `envFile` through environment variables. Set `SAIL=/path/to/sail` if `sail` isn't on your PATH.
  Never run `sail` with `--debug`: it saves that setting and prints access tokens.
- The build output (`dist/bulk-access-request-plugin/browser/`) is several files: `index.html`, `main-*.js`, `styles-*.css` and
  `chunk-*.js`. Each tab (and demo mode) is a lazy chunk that `main-*.js` loads by a relative path, so only the shell counts
  toward the 1 MB initial budget. `sail ui-plugins upload` uploads every file in that folder, keeping their paths, so upload the
  whole folder from a fresh build; never copy just `main-*.js`.

Then open the plugin: `https://<tenant>.identitynow.com/ui/plugin/<plugin id>` (the installer prints it). To put it in the menu:
**Admin → Global → System Settings → Customize Navbar → Custom Item → Destination: Plugin.**

### Go live

When dry-run behaves, set `"mode": "live"` in the config and run `install.py` again. The page shows a **Dry run** tag in its header while the workflow is in dry-run mode.

## Use

- **New request.** Work through the four steps. Each step checks its input as you go:
  - the INC number is checked against the pattern while you type;
  - you can't choose yourself as approver;
  - the item and people limits come from the config (no people limit by default);
  - the duration or end date of temporary access is checked against the config;
  - the justification is kept short enough for SailPoint's 150-character approval comment.
  
  On the review step the page shows the access choice and, for big requests, the parts, and warns you if someone already has,
  or has already requested, an item (up to 100 people). After you submit, it shows each part's workflow execution and approval,
  a summary ("2 of 3 approved · 1 waiting"), and keeps checking until the approver decides.
- **Pasting a long list.** Identity IDs go 50 at a time to `/v2025/identities?filters=id in (…)`; usernames and emails go 50 at a time
  as `alias eq "…" or email eq "…"` (that API only allows `eq` on alias and email; both are case-insensitive). Entries that
  list misses, such as identities not indexed yet, are then looked up through identity search and accounts
  (`name in`, `nativeIdentity in`, `identityId in`), and IDs one by one as a last resort. A progress bar shows how far it got.
  **Without ORG_ADMIN** those identity, search and accounts calls are refused (403), so search and pasted lists use
  `/v3/public-identities` instead (same `id in` / `alias eq` / `email eq` batches; type-ahead by display name, username, email,
  first or last name). It has no fallbacks, so someone it doesn't list stays unresolved; and the "already has it" check
  covers access profiles and roles only (entitlements need admin rights; the review step says so).
  The chosen people show as a filtered, paged list.
- **My bulk requests.** This tab shows your requests grouped by INC: the approvals (who decides, the decision, when; the parts of a
  split request together, with "2 of 3 approved") and, once approved in live mode, every access request with its status and,
  for temporary access, **Temporary until …**. It joins two lists: approvals named `Bulk access <INC>` (or `… (k/n)`) that you
  requested, and access requests you filed whose comment carries an INC number. Requests made through the Launcher show up here too.

The approver decides in ISC as usual (**Approvals → Other**: it's a generic approval, not an access request), or an admin can decide for them through `POST /v2025/generic-approvals/{id}/approve` or `/reject`.
- **Approvals.** Item owners and managers decide the per-person approvals a bulk request created, one INC at a time (see *Approvals tab*).

## Uninstall

```bash
python plugin/uninstall.py --config config/<tenant>.json --plugin   # asks before deleting; --yes to skip
```

This deletes only the workflow named exactly `<prefix> Bulk Access Request (Plugin)` and, with `--plugin`, the plugin whose alias and name match
the config. It refuses anything whose name doesn't start with the prefix. It doesn't touch access requests or approvals that already exist.

## Develop and test

```bash
cd plugin
npx ng test --watch=false        # unit tests (vitest): rules port, API calls, grouping, submission flow
npm start                        # https://localhost:4200, for `sail ui-plugins link` development inside ISC
npm run start:demo               # then open http://localhost:4300/?demo=review  (no tenant needed)
cd .. && python -m pytest plugin/tests -q    # installer tests (dry-run payloads, idempotency, safe uninstall)
```

**Demo mode.** `?demo=<scenario>` runs the page on its own with made-up data. The scenarios are `new`, `people` (600 people:
3 parts), `items`, `approver`, `approver-error`, `temporary`, `review`, `parts-review`, `submitted`, `parts-submitted` (both submit
through the Launcher, the default), `submitted-test-endpoint` (the same through the workflow test endpoint), `launcher-denied`
(a user without the Launcher Access profile submits), `history`,
`approvals` (a non-admin item approver with 300 + 40 approvals from two bulk requests, and 3 others) and `approvals-partial`
(the same, but some calls are throttled or fail, a colleague decides some first, and a few stay pending). Demo mode is ignored inside ISC, where the page always runs in an iframe, and its code
loads only when `?demo=` is in the URL (`src/main.ts` imports it on demand). The screenshots above
come from it, taken by [tools/demo-capture](../tools/demo-capture/README.md), which also records the demo video.

**The rules match the core.** `src/app/bulk/rules.ts` is a port of `core/bulkaccess/rules.py` (request validation, INC check, approver ≠ requester,
catalog filter, `splitIntoParts` / `partLabel`, temporary-access checks and the `removeDuration` and label conversions). `rules.spec.ts` mirrors `core/tests/test_core.py`. If you change a rule, change both.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| "SailPoint refused the call (HTTP 403)" on submit (test-endpoint mode) | You aren't allowed to test workflows. Use the Launcher, or switch to `plugin.submit: "launcher"`. |
| "The workflow … is not installed" (test-endpoint mode) | Run `install.py` against this tenant. The page finds the workflow by ID (from the runtime config) or by name. |
| Someone can't be found by search | New identities can take a while to reach the search index. The page also looks people up through their accounts. If that fails too, paste their identity ID. |
| An access request shows **Cancelled: "Already has a pending request for this item"** | That person already had an open request for the item. SailPoint skips duplicates. |
| The workflow run **Failed** and no approval appeared | The workflow's own checks stopped it: the INC was invalid or the approver was the requester. The requester gets an email (test-endpoint mode); through the Launcher, the page shows the Launcher's message. |
| `sail` prints "Secrets storage is not currently functional" | This is harmless. The scripts pass the PAT through environment variables. |
| "Part 2 didn't start" after submitting a big request | That part's launch or form submission (or test-endpoint call) failed; the message says why. The other parts are unaffected; press **Retry part 2**. |
| "To submit, you need '… Launcher Access'" | The user doesn't hold the Launcher Access profile (yet). They request it in the Request Center; once it's provisioned (about a minute), submit again. |
| "The Launcher form refused the request (…)" | The Launcher form's own checks refused a value (e.g. the INC). Nothing was sent for approval; fix it and submit again. |
| "…its form didn't appear within 30 seconds" | The Launcher started but its workflow didn't show the form. Check that the Launcher's workflow is enabled (`bulkaccess.py status`). |
| "Configuration problem: launcherId is missing" | The runtime config was not written by `plugin/install.py` (the committed copy has no Launcher ID). Run `bulkaccess.py apply`. |
| An approver can't open the plugin | It's private. Make it public (`plugin.public`) or add them to the plugin's `restrictToUsers`. |
| No Approvals tab | `approvals.enabled` is false, or the runtime config predates the tab: re-run `install.py`. |
| Approvals tab: "decided by someone else" | A colleague in the same governance group, or an admin, decided first. Nothing to do. |
| Approvals tab: "still pending" after approving | SailPoint accepted the call but hadn't updated the approval when the page checked. **Refresh** later, or **Retry**. |
| Temporary access isn't offered | `temporaryAccess.enabled` is false, or the runtime config predates temporary access: re-run `install.py`. |

## Files

| Path | What it is |
|---|---|
| `install.py`, `status.py`, `uninstall.py`, `pluginlib.py` | The installer scripts. They use the shared core in `../core/bulkaccess/`. |
| `tests/` | pytest tests for the installer. |
| `sp-ui-plugin.json`, `public/bulk-access.config.json` | The manifest and runtime config, both generated by `install.py`. |
| `src/app/bulk/` | Rules port, runtime config, API calls, request state, the grouping logic for My bulk requests, and the Approvals tab's grouping (`approvals.ts`) and state (`approvals-store.ts`). |
| `src/app/features/` | The three tabs: `new-request/` (four steps), `my-requests/` and `approvals/`. |
| `src/app/core/` | SailPoint plugin SDK wrapper, from the official Angular starter. |
| `src/app/demo/` | Demo mode and its made-up fixtures, also used by the unit tests. `scenario.ts` (the `?demo=` check) is the only part in the initial bundle. |
