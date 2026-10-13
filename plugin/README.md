# Bulk Access Request: UI plugin (deployment B)

This is a page inside SailPoint Identity Security Cloud (ISC) for requesting access for many people at once:

1. **People.** Search for people, or paste a list of identity IDs, usernames or email addresses. There's no limit:
   paste 1,000 or more and the page looks them up in batches.
2. **Access.** Pick one or more items from the Request Center catalog. Items SailPoint grants without any approval are
   labelled *"No approval: granted right away"*.
3. **INC & details.** Enter the ServiceNow incident number (INC) and a justification, and say how long the access should
   last: permanently, for a duration, or until a date.
4. **Review and submit.** The page files the access requests **as you**: one per person, each with all the chosen items,
   and the INC number in every item's comment.

There is no approval of the bulk request itself. Each access request goes through **its item's own approval**, as
SailPoint has it set up (owner, manager, …); an item with no approval of its own is granted right away, and the review
step warns about those. A second tab, **My bulk requests**, lists the access requests you filed, grouped by INC, with
who each one waits for. A third tab, **Approvals**, is for the *item* approvers (owners, managers) those requests reach:
it groups their pending approvals by INC and decides a whole bulk request at once
(see [Approvals tab](#approvals-tab-item-approvers)).

## Limits: no people limit, 10 people per request

- **People:** no limit by default (`people.max: null`). Set `people.max` in the config if you want one.
- **Requests:** SailPoint takes at most **10** people per access request, so the page sends the list as several requests,
  3 at a time, each with every item. **Every request carries the same INC**, items, justification and access choice.
- **Through the Launcher** (`plugin.submit: "launcher"`), SailPoint's workflow loop refuses more than **250** items, so one
  Launcher run covers at most 250 people (`people.partSize`, 1 to 250). A bigger request is sent as several runs, one after
  another, with the part label `(1/3)`, `(2/3)`, … The page says so on the People step ("600 people → sent as 3
  Launcher runs of up to 250, same INC") and lists the runs on the review step.
- **If some requests (or runs) can't be sent**, the submitted view says which ones, and **Retry** sends just those again.
  A permission error (401/403) stops the rest.
- **Items:** at most 25 per request (SailPoint's limit), `catalog.maxItems`.
- **Justification:** at most 1,000 characters.
- With more than 100 people, the review step skips the "who already has this" check (it costs one call per person).
  SailPoint skips what people already have anyway, and says so: the page reports how many were already pending.

## Temporary access

On the INC & details step, *How long should the access last?* offers:

| Choice | Sent directly (`removeDate` on each item) | Sent through the Launcher (`removeDuration`) | Label |
|---|---|---|---|
| Permanent | none | `""` | `Permanent` |
| For a duration: a whole number and a unit (hours, days, weeks, months) | now + the duration (a month is a calendar month) | e.g. `30d`, `2h`, `1w`, `3M` | `Temporary: 30 days` |
| Until a date (after today) | 23:59:59 that day, your local time | the hours from now to then, e.g. `734h` | `Temporary: until 2026-11-07` |

SailPoint removes the access automatically on its remove date. The date is set when you submit, so a slow item approval
doesn't move it. The config's `temporaryAccess` settings decide what is offered: `enabled`, `allow` (`duration`,
`endDate`), `units` and `maxDays` (a cap; `null` = none). When `enabled` is false the section is hidden. The checks and
their messages are the same as the core's (`rules.ts` mirrors `rules.py`). My bulk requests shows **Temporary until …** on
every access request that has an end date. The Launcher form offers durations only (a workflow can't turn a form date
into a duration).

## Approvals tab (item approvers)

Each access request a bulk request files goes through the item's
own approval scheme (owner, manager, source owner, governance group), so 300 people × 1 owner-approved item means 300
approvals for that owner. SailPoint's own Approvals page shows them one card at a time. The **Approvals** tab lists the
signed-in user's pending access-request approvals **grouped by the INC** in their comment, and decides them together:

- **Group cards** show the INC, the number of approvals, people and items, the access (`Permanent` or `Temporary: …`), who
  asked, the oldest created date and the earliest due date. Approvals from before the bulk approver was removed (their
  comment also has `Approved by …`) group the same way.
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
- **Your own decision.** Each approval is decided through `POST /generic-approvals/v1/{id}/approve|reject` as the signed-in
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
that knows about it. But an approver can only open the plugin if they can see it: make it public (`plugin.public: true`), or keep it private and add the
approvers to the plugin's `restrictToUsers`. `show-config` warns when the tab is on and the plugin is private.

The same code installs into any tenant. Everything tenant-specific (names, INC rule, limits, catalog filter) comes
from a config file, and nothing is hard-coded.

## Screenshots

### In ISC
Real screens from a test tenant (prefix `UCSF`): the Approvals tab, used by a non-admin item approver. The whole
lifecycle, with the ISC screens around the plugin, is in [USAGE.md](../USAGE.md) and
[docs/sailpoint/END_TO_END_WALKTHROUGH.md](../docs/sailpoint/END_TO_END_WALKTHROUGH.md).

| | |
|---|---|
| ![Approvals tab: one INC waiting](../docs/screenshots/isc-40-plugin-approvals.png) | ![Approvals tab: approved, confirmed](../docs/screenshots/isc-43-plugin-approvals-result.png) |

### With demo data
The whole page, including large requests and partial results, made with the plugin's demo mode:

| | |
|---|---|
| ![People step: 600 people](../docs/screenshots/plugin-1-people.png) | ![Access step](../docs/screenshots/plugin-2-items.png) |
| ![INC and details](../docs/screenshots/plugin-3-inc-details.png) | ![Validation](../docs/screenshots/plugin-3b-inc-validation-error.png) |
| ![Temporary access](../docs/screenshots/plugin-7-temporary-access.png) | ![Review of a request sent through the Launcher in 3 parts](../docs/screenshots/plugin-8-parts-review.png) |
| ![Review, with the no-approval warning](../docs/screenshots/plugin-4-review.png) | ![Requested](../docs/screenshots/plugin-5-submitted.png) |
| ![My bulk requests: access requests by INC, temporary access](../docs/screenshots/plugin-6-my-bulk-requests.png) | ![Approvals tab: pending approvals grouped by INC](../docs/screenshots/plugin-9-approvals-groups.png) |
| ![Approvals tab: one INC opened, one approval left out](../docs/screenshots/plugin-10-approvals-drilldown.png) | ![Approvals tab: confirm dialog](../docs/screenshots/plugin-11-approvals-confirm.png) |
| ![Approvals tab: decisions going out](../docs/screenshots/plugin-12a-approvals-progress.png) | ![Approvals tab: all confirmed](../docs/screenshots/plugin-12-approvals-progress-result.png) |
| ![Approvals tab: partly done, with Retry](../docs/screenshots/plugin-13-approvals-partial-retry.png) | *(Screenshots use made-up demo data. To retake them, see [tools/demo-capture](../tools/demo-capture/README.md).)* |

## Who can use it: Launcher Access to submit, any approver for the Approvals tab

| Who | Needs | Can use |
|---|---|---|
| **Requester** (any user) | The *"<prefix> Bulk Access Request - Launcher Access"* profile, requested in the Request Center (auto-approved with `access.launcherApproval: "NONE"`, else by their manager). It also puts the Launcher in their Launchpad | *New request*, *My bulk requests*, *Approvals* |
| **Item approver** (owner, manager, governance group) | Nothing extra, beyond seeing the plugin | *Approvals* tab, or ISC **Approvals → Access Requests** |
| **ORG_ADMIN** | — | Everything; the "already has it" check also covers entitlements |

Everyone must be able to see the plugin: make it public (`plugin.public: true`), or add them to its `restrictToUsers`.

Before it sends anything, the page checks that the signed-in user holds the *Launcher Access* profile
(`GET /requestable-objects/v1?identity-id=<me>&types=ACCESS_PROFILE&filters=id eq "<profile id>"` answers
`requestStatus: ASSIGNED`; the installer writes the profile's ID into the runtime config). Someone without it gets
**"To submit, you need '*<prefix> Bulk Access Request - Launcher Access*'. Request it in the Request Center."** and
nothing is sent. Without the Launcher deployment there is no such profile, and anyone who can open the plugin can submit.

How the page submits is set by `plugin.submit` in the config (`show-config` says which applies):

**`"direct"` (the default): the page files the access requests as the signed-in user.**
For each group of up to 10 people it calls `POST /access-requests/v1` with the user's own ISC session: no ORG_ADMIN,
no workflow, no backend. Every item carries the bulk comment and, for temporary access, its `removeDate`. SailPoint
answers with the requests it filed (`newRequests`) and the ones it skipped because the same request was already open
(`existingRequests`); the page shows both counts. The requests carry the user as requester, so they're in the user's
Request Center and *My bulk requests*, and ISC never lets the user approve their own request (it escalates such an
approval to an admin). This needs the tenant to let users request access for others (**Admin → Global → Access Request
Settings**, *Request for others*); verified live as a non-admin (see `docs/dev/CONTRACTS.md` §3). A refusal says so:
*"SailPoint refused the request (HTTP 403). Your account may not be allowed to request access for these people…"*.

**`"launcher"` (the installer's fallback when the tenant doesn't allow that): the page drives the Launcher deployment.**
It uses `../launcher/`, as the signed-in user. For each part it

1. starts the Launcher: `POST /launchers/v1/{id}/launch` (the installer writes the Launcher's ID into the runtime config,
   because people who aren't admins can't list launchers);
2. waits for its form: `GET /beta/interactive-processes/{id}/blocks` until the FORM block names the form instance (up to
   30 s). This is the plugin's only beta call: there is no GA path for a Launcher run's blocks;
3. fills in and submits that form: `PATCH /form-instances/v1/{id}` with the people, items, INC, justification, access
   fields and the part label (`" (2/3)"`, an extra form data key). The page checks everything first with the same rules as the form;
4. reads the run's answer from the same blocks: its *Requested* (or, dry run, *Checked*) message, or the error that
   stopped it. If none comes within 2 minutes, the page says an email will follow.

The Launcher's workflow then runs exactly as from the Launchpad: it files the requests as the **workflow owner**, so they
don't appear in the requester's Request Center or *My bulk requests*, and it emails the requester. The 30-person and
catalog limits of the Launchpad's form are its pickers' only: through the API a part carries up to 250 people (verified
live). An end date is sent as a number of hours in the form's duration fields, so the comment says `Temporary: 720h`
rather than `Temporary: until …`.

`"test-endpoint"`, the old ORG_ADMIN-only mode through a disabled plugin workflow, is gone. A config or runtime config
that still says so is read as `"direct"` (`show-config` prints a note).

## What gets installed

All names come from `prefix` in the config (here `ACME`), so several copies can live in one tenant and you can always
tell them apart:

| Object | Name | Notes |
|---|---|---|
| UI plugin | alias `plugin.alias` (default `<prefix>-bulk-access`), name `plugin.displayName` | Created **private** (only you can see it) unless `plugin.public` is true or you pass `--public`. |

Nothing else: the page uses the Launcher deployment's *Launcher Access* profile (and, in launcher mode, its Launcher; see
[INSTALL.md](../INSTALL.md)). An older install's disabled workflow `<prefix> Bulk Access Request (Plugin)` is no longer
used; `install.py` and `status.py` point it out and `uninstall.py` removes it.

In `dry-run` mode the page checks everything and requests nothing (through the Launcher, its workflow runs its checks and
emails). Start in dry-run.

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
   | `mode` | `dry-run` (check everything, request nothing) or `live`. |
   | `inc.pattern`, `inc.example`, `inc.message` | The INC rule, e.g. `^INC\d{7}$`. Write a pattern that means the same in Python and JavaScript (plain classes such as `\d` and `[A-Z]`, anchors, groups). |
   | `catalog.types`, `catalog.nameStartsWith`, `catalog.maxItems` | Which Request Center items the page offers (max 25 per request). |
   | `people.max`, `people.partSize` | People per bulk request (`null` = no limit) and, through the Launcher, people per run (1 to 250, default 250). See *Limits*. |
   | `temporaryAccess.*` | `enabled`, `allow` (`duration`, `endDate`), `units`, `maxDays`. See *Temporary access*. |
   | `approvals.*` | The Approvals tab: `enabled` (default true), `concurrency` (1 to 8, default 4), `useBulkEndpoint` (`auto`, `always`, `never`), `maxRows` (250 to 20,000, default 5,000), `showOther` (default false), `denyCommentRequired` (default true). |
   | `notifications.overrideRecipients` | For test tenants: send every email the Launcher's workflow sends here instead of to real people. |
   | `plugin.alias`, `plugin.displayName`, `plugin.public` | The plugin's alias (lowercase, digits, dashes), the name shown in ISC, and whether everyone can see it (default false: only you). |
   | `plugin.submit` | `"direct"` (default): file the access requests as the signed-in user. `"launcher"`: submit through the Launcher's form (needs the Launcher deployment; the installer switches to it if the tenant doesn't allow requesting for others). See *Who can use it*. |

   `approval.*`, `notifications.ccApprover` and `notifications.pendingEmail` belonged to the bulk approver, which is gone:
   they still load, are ignored, and `show-config` notes each one.

2. The installer turns this into two files the page reads. Don't edit them by hand:
   - `public/bulk-access.config.json`: the runtime config (`submit`, the *Launcher Access* profile's ID and name, the
     Launcher's ID in launcher mode, INC rule, `peopleMax`, `partSize`, `itemsMax`, catalog filter, `noApproval` (the
     catalog items SailPoint grants without approval, refreshed on every install), `temporary`). The committed copy holds
     neutral defaults (no profile ID, so it turns nobody away; no Launcher ID, so launcher mode refuses to submit). One
     without `submit`, or with the old `test-endpoint`, files directly. A runtime config without a `temporary` block
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

# 2. Look up the Launcher Access profile (and, in launcher mode, the Launcher; install the Launcher deployment first),
#    list the items without an approval, write the runtime config and manifest,
#    build the page and upload it (first time: `sail ui-plugins create --private`)
python plugin/install.py --config config/<tenant>.json --deploy

# 3. Check
python plugin/status.py --config config/<tenant>.json --plugin
```

`install.py` is idempotent: it finds the plugin by alias and updates it. Re-run it after any config change, and when the
catalog or an item's approval setup changes (that refreshes `noApproval`). In direct mode it first checks that the tenant
lets users request access for others; if not, it switches to launcher mode (with the Launcher deployment) or stops with a
message.
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

When dry-run behaves, set `"mode": "live"` in the config and run `install.py` again. The page shows a **Dry run** tag in its header while the installation is in dry-run mode.

## Use

- **New request.** Work through the four steps. Each step checks its input as you go:
  - the INC number is checked against the pattern while you type;
  - the item and people limits come from the config (no people limit by default);
  - the duration or end date of temporary access is checked against the config;
  - the justification is required, up to 1,000 characters.

  On the review step the page shows the access choice, warns about items with no approval of their own, and warns you if
  someone already has, or has already requested, an item (up to 100 people). Through the Launcher it also lists the parts.
  After you submit, it shows how many access requests were filed (or each Launcher run's answer), and which requests failed
  to send, with **Retry**.
- **Pasting a long list.** Identity IDs go 50 at a time to `/identities/v1?filters=id in (…)`; usernames and emails go 50 at a time
  as `alias eq "…" or email eq "…"` (that API only allows `eq` on alias and email; both are case-insensitive). Entries that
  list misses, such as identities not indexed yet, are then looked up through identity search and accounts
  (`name in`, `nativeIdentity in`, `identityId in`), and IDs one by one as a last resort. A progress bar shows how far it got.
  **Without ORG_ADMIN** those identity, search and accounts calls are refused (403), so search and pasted lists use
  `/public-identities/v1` instead (same `id in` / `alias eq` / `email eq` batches; type-ahead by display name, username, email,
  first or last name). It has no fallbacks, so someone it doesn't list stays unresolved; and the "already has it" check
  covers access profiles and roles only (entitlements need admin rights; the review step says so).
  The chosen people show as a filtered, paged list.
- **My bulk requests.** This tab lists the access requests you filed (`GET /access-request-status/v1?requested-by=<me>`,
  newest first, up to 1,000), grouped by the INC in their comment: per INC the access, the justification and counts
  (waiting, done, not granted), and per person × item its status (**Waiting for <approver>** while an item approval is
  pending, **Provisioning**, **Done**, **Rejected**, …) and, for temporary access, **Temporary until …**. Requests sent
  through the Launcher are filed by its workflow owner, so they don't show here.
- **Approvals.** Item owners and managers decide the per-person approvals a bulk request created, one INC at a time (see *Approvals tab*).

## Uninstall

```bash
python plugin/uninstall.py --config config/<tenant>.json --plugin   # asks before deleting; --yes to skip
```

This deletes, with `--plugin`, the plugin whose alias and name match the config, and the old workflow named exactly
`<prefix> Bulk Access Request (Plugin)` if an earlier install left one. It refuses anything whose name doesn't start with the prefix. It doesn't touch access requests or approvals that already exist.

## Develop and test

```bash
cd plugin
npx ng test --watch=false        # unit tests (vitest): rules port, API calls, grouping, submission flow
npm start                        # https://localhost:4200, for `sail ui-plugins link` development inside ISC
npm run start:demo               # then open http://localhost:4300/?demo=review  (no tenant needed)
npm run start:sandbox            # like npm start, but on 0.0.0.0 (reachable from another machine or a container)
npm run watch                    # development build that rebuilds on every change
cd .. && python -m pytest plugin/tests -q    # installer tests (dry-run payloads, idempotency, safe uninstall)
```

**Demo mode.** `?demo=<scenario>` runs the page on its own with made-up data. The scenarios are `new`, `people` (600
people), `items`, `details`, `inc-error` (a mistyped INC), `temporary`, `review` (one item has no approval: the warning),
`submitted` (filed directly, the default), `parts-review` and `parts-submitted` (600 people through the Launcher: 3 runs
of 250, temporary), `launcher-denied` (a user without the Launcher Access profile submits), `history`, `approvals` (a
non-admin item approver with 300 + 40 approvals from two bulk requests, and 3 others) and `approvals-partial` (the same,
but some calls are throttled or fail, a colleague decides some first, and a few stay pending). Demo mode is ignored inside ISC, where the page always runs in an iframe, and its code
loads only when `?demo=` is in the URL (`src/main.ts` imports it on demand). The screenshots above
come from it, taken by [tools/demo-capture](../tools/demo-capture/README.md), which also records the demo video.

**The rules match the core.** `src/app/bulk/rules.ts` is a port of `core/bulkaccess/rules.py` (request validation, INC and justification checks,
catalog filter and the no-approval label, the bulk comment and reading it back (both forms), `splitIntoParts` / `partLabel`, temporary-access checks and the `removeDuration`, `removeDate` and label conversions). `rules.spec.ts` mirrors `core/tests/test_core.py`. If you change a rule, change both.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| "SailPoint refused the request (HTTP 403)…" on submit | The tenant doesn't let this user request access for others, or the session expired (reload). Allow *Request for others* in Access Request Settings, or set `plugin.submit: "launcher"` and run `install.py`. |
| Someone can't be found by search | New identities can take a while to reach the search index. The page also looks people up through their accounts. If that fails too, paste their identity ID. |
| An access request shows **Cancelled: "Already has a pending request for this item"** | That person already had an open request for the item. SailPoint skips duplicates. |
| Everyone got an item at once | It has no approval of its own in SailPoint (the page labelled it *No approval*). Give it an approval scheme if it needs one, then re-run `install.py`. |
| "The Launcher stopped it: …" | Launcher mode: the workflow's own checks refused the request (e.g. the INC or the duration). Nothing was requested; the requester also gets a *Not requested* email. |
| `sail` prints "Secrets storage is not currently functional" | This is harmless. The scripts pass the PAT through environment variables. |
| "part 2 weren't sent" after submitting a big request | That request (or Launcher run) failed; the message says why. The other parts are unaffected; press **Retry part 2**. |
| "To submit, you need '… Launcher Access'" | The user doesn't hold the Launcher Access profile (yet). They request it in the Request Center; once it's provisioned (about a minute), submit again. |
| "The Launcher form refused the request (…)" | The Launcher form's own checks refused a value (e.g. the INC). Nothing was requested; fix it and submit again. |
| "…its form didn't appear within 30 seconds" | The Launcher started but its workflow didn't show the form. Check that the Launcher's workflow is enabled (`bulkaccess.py status`). |
| "This page doesn't know which Launcher to use yet (launcherId is missing)…" | Launcher mode, but the runtime config was not written by `plugin/install.py` (the committed copy has no Launcher ID). Run `bulkaccess.py apply`. |
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
