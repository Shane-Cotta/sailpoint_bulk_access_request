# Bulk Access Request for SailPoint Identity Security Cloud

Request access for **many people at once**, tracked by a **ServiceNow INC number**. Each item is approved the way
SailPoint already has it set up (its owner, the person's manager, …); there is no extra approval for the bulk request.

> *"These 12 new contractors all need the same three access profiles for INC0012345, for 30 days."*

1. **Pick the people:** search for them, or paste a list (the plugin accepts pasted usernames or emails).
2. **Pick the access:** one or more items from the Request Center catalog.
3. **Choose how long:** permanent, or temporary access that SailPoint removes automatically.
4. **Enter the INC number** (checked for the right format) and a justification.
5. **Submit.** Everyone is requested every item as normal SailPoint access requests, and each request's comment reads
   `INC… | Bulk access request by … | <Permanent or Temporary: …> | <justification>`.

**Each item's own approval applies.** An item with an approval scheme (owner, manager, …) needs one approval per person.
The UI plugin's **Approvals** tab lets those approvers, admin or not, see their pending approvals grouped by INC and
approve or deny a whole bulk request at once, each recorded as their own decision (see [plugin/README.md](plugin/README.md)
and the [Approvals screenshots](#screenshots)). An item with **no** approval of its own is granted right away; the
installer finds those items and the plugin warns about them before you submit.

## Two ways to deploy it (one config, one command)

| | **A. Launcher** (native) | **B. UI plugin** |
|---|---|---|
| Where users find it | **Launchpad**, as a native SailPoint form | A page inside ISC, reachable from a nav-bar link |
| Who can use it | **Any user** you give the *Launcher Access* profile to (they can request it in the Request Center) | **Any user** you give the *Submit Access* role to (they request it in the Request Center; with A deployed it includes the *Launcher Access* profile, so one request covers both). By default (`plugin.submit: "direct"`) the plugin files the access requests **as the signed-in user**; if the tenant doesn't let users request for others, it submits through the Launcher instead (`"launcher"`) |
| People | Form picker, **up to 30** (SailPoint's form limit) | Search or paste a list, **no limit**: sent 10 people per access request (SailPoint's limit), or 250 per Launcher run. Warns about access people already have. |
| Temporary access | By **duration**: hours, days, weeks or months | By **duration** or **end date** |
| Requester of the item requests | The workflow owner (the admin account the tool was installed with) | **You**, so they're in your Request Center (through the Launcher: the workflow owner) |
| Status tracking | A "Requested" email | **My bulk requests** tab: your access requests grouped by INC, with who each one waits for |
| Item approvers | ISC **Approvals → Access Requests**, one card per person | The same, or the **Approvals** tab: a whole INC at once, for any approver who can see the plugin |
| Docs | [INSTALL.md](INSTALL.md), [USAGE.md](USAGE.md) | [plugin/README.md](plugin/README.md) |

**One settings file drives both.** You copy `config/bulk-access.example.json` to `config/<tenant>.json`, choose which
deployments you want, and run:

```bash
python bulkaccess.py apply --config config/<tenant>.json --dry-run    # preview; changes nothing
python bulkaccess.py apply --config config/<tenant>.json              # install or update
python bulkaccess.py status --config config/<tenant>.json             # check
```

**Not sure which?** Start with **A**: it works for everyone and needs nothing but this folder. Add **B** if your
requesters want the richer screen or bigger lists. Both run side by side in one tenant. With both, people request one
thing, the plugin's *Submit Access* role: it contains A's *Launcher Access* profile, so it opens the plugin's submit and
the Launchpad at once (the profile is then not requestable on its own). With A alone, they request the *Launcher Access*
profile.

## What gets created in the tenant
Everything is named with your **prefix** (for example `ACME`), so it is easy to find and to remove:

- **A:** *"ACME Bulk Access Request Form"*, the *"ACME Bulk Access Request"* workflow and Launcher, and the
  *"ACME Bulk Access Request - Launcher Access"* access profile.
- **B:** the *ACME Bulk Access Request* UI plugin and the requestable *"ACME Bulk Access Request - Submit Access"* role
  (who may submit; with A it contains the Launcher Access profile). (An older install's disabled
  *"ACME Bulk Access Request (Plugin)"* workflow is no longer used; `uninstall` removes it.)

Temporary access and large requests need nothing extra: SailPoint removes temporary access on its own, and a large
plugin request is simply several access requests (or Launcher runs) with the same INC.

## Safe by default
- **Preview first.** `python bulkaccess.py apply --dry-run` prints everything it would send and changes nothing.
- **New installs start in `dry-run` mode.** The whole flow runs (form, checks, emails), but nothing is requested
  until you switch `"mode": "live"`.
- **The catalog can be limited** to item names starting with a given text (`catalog.nameStartsWith`).
- **Temporary access can be capped** (`temporaryAccess.maxDays`), limited to some units, or turned off.
- **All email can be redirected** to a test inbox (`notifications.overrideRecipients`).
- **Items without an approval are flagged:** the installer lists every catalog item SailPoint would grant without
  approval, and the plugin labels them *"No approval: granted right away"* and warns on the review step.
- **The plugin is uploaded private** (visible only to you) unless you set `plugin.public`.

## For SailPoint and platform teams
Before installing in a production tenant, check that the tenant offers everything the tool uses:

| Document | What's in it |
|---|---|
| [API inventory](docs/sailpoint/API_INVENTORY.md) | Every API call (method, path, version, caller, user level, scopes), workflow step, form feature and `sail` command |
| [API contract alignment](docs/sailpoint/API_CONTRACT_ALIGNMENT.md) | How each call maps to SailPoint's GA `/<service>/v1` paths (what the tool uses), the older versions and the experimental APIs; known deviations from the spec |
| [Requirements checklist](docs/sailpoint/REQUIREMENTS_CHECKLIST.md) | A tick-box list of features, user levels, scopes, settings, limits and tooling, each with a one-line check |
| [Segments and user levels](docs/sailpoint/SEGMENTS_AND_USER_LEVELS.md) | How to let chosen people request restricted access (for example privileged groups) with access request segments, set up in the ISC UI; and which user levels, built-in or custom, may set it up |
| [End-to-end walkthrough](docs/sailpoint/END_TO_END_WALKTHROUGH.md) | The full lifecycle on a real tenant, step by step: who acts, where in ISC, what they see, and which API or ISC feature is used; plus what each person's Request Center shows before and after approval |

## Screenshots
Real SailPoint screens (`docs/screenshots/isc-*`, `launcher-*`) from a test installation that used the prefix `UCSF`
(yours show your own prefix), and plugin screens made with demo data (`plugin-*`). [USAGE.md](USAGE.md) walks through them in order.

| 1. Get the tool (Request Center) | 2. Submit from the plugin |
|---|---|
| ![Request Launcher Access in the Request Center](docs/screenshots/isc-01-request-launcher-access.png) | ![The plugin's review step](docs/screenshots/isc-13-plugin-review.png) |
| *Requesters ask for access once (this older screen shows "Launcher Access"; now it's "Submit Access")* | *People, access, INC, review: filed as the signed-in user* |

| 3. Item approvers (plugin Approvals tab) | 4. Track it (plugin My bulk requests) |
|---|---|
| ![The plugin's Approvals tab, one INC opened](docs/screenshots/isc-41-plugin-approvals-open.png) | ![My bulk requests](docs/screenshots/isc-15-plugin-my-bulk-requests-waiting.png) |
| *Item owners decide a whole INC at once (or one card per person in ISC)* | *Your access requests by INC, with who each one waits for* |

More plugin screens (made with sample data, including large requests sent in parts) are in [plugin/README.md](plugin/README.md).
The demo videos below were recorded before the bulk approver was removed: they still show its step and approval.

**Demo videos.** Attached to GitHub releases:
- [**demo-2026-10-09**](https://github.com/Shane-Cotta/sailpoint_bulk_access_request/releases/tag/demo-2026-10-09):
  `bulk-access-real-e2e.mp4` (5½ min), a real end-to-end run in a live ISC tenant with test identities: Launcher Access
  from the Request Center, a submit from the plugin as a non-admin, the bulk approval in ISC Approvals, the item approvals
  in ISC and in the plugin's Approvals tab, and what each person sees afterwards.
- [**demo-2026-10-08**](https://github.com/Shane-Cotta/sailpoint_bulk_access_request/releases/tag/demo-2026-10-08):
  `bulk-access-demo.mp4`, a captioned tour of the UI plugin in demo mode (new request, My bulk requests, the Approvals
  tab, large requests). [tools/demo-capture](tools/demo-capture/README.md) records it.

## Folder layout
```
bulk-access-request/
├── README.md  INSTALL.md  USAGE.md        ← start here
├── bulkaccess.py                          ← the one command: show-config · apply · status · uninstall · export
├── config/bulk-access.example.json        ← copy to config/<tenant>.json (your copies stay out of git)
├── core/        shared Python package: config, API client, rules, definitions (+ tests)
├── launcher/    deployment A: install.py · status.py · uninstall.py · e2e.py
├── plugin/      deployment B: the Angular UI plugin + its install/status/uninstall
├── tools/       demo-capture/: scripted screenshots and the demo video, from the plugin's demo mode
└── docs/        screenshots/ · dev/ (developer notes) · sailpoint/ (API inventory, requirements)
```

## Tested
Both deployments were installed in a live Identity Security Cloud tenant and verified end to end with test identities and a
test access profile. Before the bulk approver was removed:
- **Launcher:** dry-run and live runs, one real request per person carrying the INC in its comment; temporary access
  (live, 1 day, `removeDate` +1 day on every request; permanent: none); an invalid duration, or one over `maxDays`,
  stopped before anything is requested.
- **UI plugin:** live runs up to 250 people per run, parts with one INC, temporary access by end date and by duration,
  invalid INC, and a submit through the Launcher by a **non-admin** requester who got *Launcher Access* from the Request
  Center (recorded: see *Demo videos*).

For the design without it, verified live as a non-admin: `POST /access-requests/v1` for others (up to 10 people, all three
item types, per-item comment and `removeDate`), reading those requests back with `/access-request-status/v1?requested-by=`,
items without an approval scheme granted at once, and the submit check through `/requestable-objects/v1` (for the
*Launcher Access* profile then, and for the *Submit Access* role since, `types=ROLE`).

**Unit tests:** core and plugin installer 320 (`pytest`), plugin UI 203 (`ng test`), none of them needing a tenant.
