# Bulk Access Request for SailPoint Identity Security Cloud

Request access for **many people at once**, approved by **one person you choose**, and tracked by a
**ServiceNow INC number**.

> *"These 12 new contractors all need the same three access profiles for INC0012345, for 30 days. Have Dana approve it once."*

1. **Pick the people:** search for them, or paste a list (the plugin accepts pasted usernames or emails).
2. **Pick the access:** one or more items from the Request Center catalog.
3. **Choose how long:** permanent, or temporary access that SailPoint removes automatically.
4. **Pick one approver** for the whole request. It can't be you, or one of the people getting the access.
5. **Enter the INC number** (checked for the right format) and a justification.
6. **Submit.** The approver gets **one** approval task (one per 250 people in the plugin), in SailPoint's own
   **Approvals → Other**. If they approve, everyone is
   requested every item as a normal SailPoint access request, and each request's comment reads
   `INC… | Bulk access request by … | Approved by … | <Permanent or Temporary: …> | <justification>`.
   If they deny, nothing is requested. Either way, the requester gets an email.

**Item approvals still apply.** Items with their own approval scheme (owner, manager, …) then need one approval per
person. The UI plugin's **Approvals** tab lets those approvers, admin or not, see their pending approvals grouped by INC
and approve or deny a whole bulk request at once, each recorded as their own decision (see [plugin/README.md](plugin/README.md)
and the [Approvals screenshots](#screenshots)).

## Two ways to deploy it (one config, one command)

| | **A. Launcher** (native) | **B. UI plugin** |
|---|---|---|
| Where users find it | **Launchpad**, as a native SailPoint form | A page inside ISC, reachable from a nav-bar link |
| Who can use it | **Any user** you give the *Launcher Access* profile to (they can request it in the Request Center) | **The same users**: with A installed (the default, `plugin.submit: "launcher"`), the plugin submits through the Launcher as the signed-in user. Without A, ORG_ADMIN only (see the plugin README) |
| People | Form picker, **up to 30** (SailPoint's form limit) | Search or paste a list, **no limit**. Above 250 people, the request is sent as several approvals with the same INC (SailPoint's workflow loop limit). Warns about access people already have. |
| Temporary access | By **duration**: hours, days, weeks or months | By **duration** or **end date** |
| Bulk approver | ISC **Approvals → Other** ("Grant: Bulk access INC…"), plus an email | The same |
| Status tracking | Approval email. The item requests are filed by the workflow, so they're in the *workflow owner's* Request Center, not the requester's | **My bulk requests** tab, grouped by INC (for everyone; listing the item requests needs admin rights) |
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
requesters want the richer screen or bigger lists. Both run side by side in one tenant, and by default B uses A: the plugin
fills in and submits the Launcher's form for the signed-in user, so whoever may use the Launcher may submit from the plugin.

## What gets created in the tenant
Everything is named with your **prefix** (for example `ACME`), so it is easy to find and to remove:

- **A:** *"ACME Bulk Access Request Form"*, the *"ACME Bulk Access Request"* workflow and Launcher, and the
  *"ACME Bulk Access Request - Launcher Access"* access profile.
- **B:** the *ACME Bulk Access Request* UI plugin. Only when it submits through the workflow test endpoint
  (`plugin.submit: "test-endpoint"`, the default without A) also the *"ACME Bulk Access Request (Plugin)"* workflow (kept disabled, by design).

Temporary access and large requests need nothing extra: SailPoint removes temporary access on its own, and a large
plugin request is simply several approvals.

## Safe by default
- **Preview first.** `python bulkaccess.py apply --dry-run` prints everything it would send and changes nothing.
- **New installs start in `dry-run` mode.** The whole flow runs (form, approval, emails), but nothing is requested
  until you switch `"mode": "live"`.
- **The catalog can be limited** to item names starting with a given text (`catalog.nameStartsWith`).
- **Temporary access can be capped** (`temporaryAccess.maxDays`), limited to some units, or turned off.
- **All email can be redirected** to a test inbox (`notifications.overrideRecipients`).
- **The approver is never the requester:** SailPoint would quietly reassign that approval to another admin, so it's blocked up front.
- **Nobody approves their own access:** a request whose approver is also on the people list is stopped before any approval exists.
- **The plugin is uploaded private** (visible only to you) unless you set `plugin.public`.

## For SailPoint and platform teams
Before installing in a production tenant, check that the tenant offers everything the tool uses:

| Document | What's in it |
|---|---|
| [API inventory](docs/sailpoint/API_INVENTORY.md) | Every API call (method, path, version, caller, user level, scopes), workflow step, form feature and `sail` command |
| [API contract alignment](docs/sailpoint/API_CONTRACT_ALIGNMENT.md) | How each call maps to v3, the dated versions, experimental APIs and the new `/<service>/v1` paths; known deviations from the spec; migration plan and risks |
| [Requirements checklist](docs/sailpoint/REQUIREMENTS_CHECKLIST.md) | A tick-box list of features, user levels, scopes, settings, limits and tooling, each with a one-line check |
| [End-to-end walkthrough](docs/sailpoint/END_TO_END_WALKTHROUGH.md) | The full lifecycle on a real tenant, step by step: who acts, where in ISC, what they see, and which API or ISC feature is used; plus what each person's Request Center shows before and after approval |

## Screenshots
Real SailPoint screens (`docs/screenshots/`) from a test installation that used the prefix `UCSF`; yours show your own
prefix. [USAGE.md](USAGE.md) walks through all of them in order.

| 1. Get the tool (Request Center) | 2. Submit from the plugin |
|---|---|
| ![Request Launcher Access in the Request Center](docs/screenshots/isc-01-request-launcher-access.png) | ![The plugin's review step](docs/screenshots/isc-13-plugin-review.png) |
| *Requesters ask for "Launcher Access" once; it then shows in the Launchpad too* | *People, access, one approver and INC, review: submitted as the signed-in user* |

| 3. The bulk approver (Approvals → Other) | 4. Item approvers (plugin Approvals tab) |
|---|---|
| ![The bulk approval in ISC Approvals, Other](docs/screenshots/isc-21-approvals-other-bulk.png) | ![The plugin's Approvals tab, one INC opened](docs/screenshots/isc-41-plugin-approvals-open.png) |
| *One approval, "Grant: Bulk access INC…", decided in SailPoint's own Approvals* | *Item owners decide a whole INC at once (or one card per person in ISC)* |

| 5. Track it (plugin My bulk requests) | The Launchpad form |
|---|---|
| ![My bulk requests: approved](docs/screenshots/isc-50-plugin-my-bulk-requests-approved.png) | ![The Launcher form](docs/screenshots/launcher-4-form-filled.png) |
| *Everything you've submitted, by INC* | *The same request as a native form, up to 30 people* |

More plugin screens (made with sample data, including large requests sent in parts) are in [plugin/README.md](plugin/README.md).

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
test access profile:
- **Launcher:**
  - Dry-run approve and deny.
  - Live approve: one real request per person, carrying the INC, requester and approver in its comment.
  - Live deny.
  - Self-approval stopped before any approval exists.
  - Temporary access: live, 1 day, with `removeDate` set to +1 day on every request. Permanent: no `removeDate`.
  - An invalid duration, or one over `maxDays`, is stopped before the approval.
- **UI plugin:**
  - Dry-run approve, live approve, live deny, invalid INC.
  - 250 people in one approval (the per-approval maximum).
  - 5 people sent in parts of 2: three approvals `(1/3)`–`(3/3)` with one INC. The denied part requested nothing.
  - Temporary by end date and by duration, with `removeDate` on every request. An invalid duration is stopped before the approval.
  - 15 people in one live run.
  - Submitted through the Launcher by a **non-admin** requester who got *Launcher Access* from the Request Center;
    bulk approval in ISC Approvals, item approvals in ISC and in the Approvals tab (recorded: see *Demo videos*).
- **Unit tests:** core 263, plugin installer 13, plugin UI 163 (`pytest`, `ng test`), none of them needing a tenant.
