# Bulk Access Request for SailPoint Identity Security Cloud

Request access for **many people at once**, approved by **one person you choose**, and tracked by a
**ServiceNow INC number**.

> *"These 12 new contractors all need the same three access profiles for INC0012345, for 30 days. Have Dana approve it once."*

1. **Pick the people:** search for them, or paste a list (the plugin accepts pasted usernames or emails).
2. **Pick the access:** one or more items from the Request Center catalog.
3. **Choose how long:** permanent, or temporary access that SailPoint removes automatically.
4. **Pick one approver** for the whole request. It can't be you, or one of the people getting the access.
5. **Enter the INC number** (checked for the right format) and a justification.
6. **Submit.** The approver gets **one** approval task (one per 250 people in the plugin). If they approve, everyone is
   requested every item as a normal SailPoint access request, and each request's comment reads
   `INC… | Bulk access request by … | Approved by … | <Permanent or Temporary: …> | <justification>`.
   If they deny, nothing is requested. Either way, the requester gets an email.

**Item approvals still apply.** Items with their own approval scheme (owner, manager, …) then need one approval per
person. The UI plugin's **Approvals** tab lets those approvers, admin or not, see their pending approvals grouped by INC
and approve or deny a whole bulk request at once, each recorded as their own decision (see [plugin/README.md](plugin/README.md)).

## Two ways to deploy it (one config, one command)

| | **A. Launcher** (native) | **B. UI plugin** |
|---|---|---|
| Where users find it | **Launchpad**, as a native SailPoint form | A page inside ISC, reachable from a nav-bar link |
| Who can use it | **Any user** you give the *Launcher Access* profile to (they can request it in the Request Center) | **ORG_ADMIN-level users only** (see the plugin README) |
| People | Form picker, **up to 30** (SailPoint's form limit) | Search or paste a list, **no limit**. Above 250 people, the request is sent as several approvals with the same INC (SailPoint's workflow loop limit). Warns about access people already have. |
| Temporary access | By **duration**: hours, days, weeks or months | By **duration** or **end date** |
| Status tracking | Approval email, plus the requests in Request Center → *Track My Requests* | **My bulk requests** tab, grouped by INC |
| Item approvers | SailPoint's Approvals page, one card at a time | **Approvals** tab: a whole INC at once, for any approver who can see the plugin |
| Docs | [INSTALL.md](INSTALL.md), [USAGE.md](USAGE.md) | [plugin/README.md](plugin/README.md) |

**One settings file drives both.** You copy `config/bulk-access.example.json` to `config/<tenant>.json`, choose which
deployments you want, and run:

```bash
python bulkaccess.py apply --config config/<tenant>.json --dry-run    # preview; changes nothing
python bulkaccess.py apply --config config/<tenant>.json              # install or update
python bulkaccess.py status --config config/<tenant>.json             # check
```

**Not sure which?** Start with **A**: it works for everyone and needs nothing but this folder. Add **B** if your
admins or service desk want the richer screen or bigger lists. Both can run side by side in one tenant.

## What gets created in the tenant
Everything is named with your **prefix** (for example `ACME`), so it is easy to find and to remove:

- **A:** *"ACME Bulk Access Request Form"*, the *"ACME Bulk Access Request"* workflow and Launcher, and the
  *"ACME Bulk Access Request - Launcher Access"* access profile.
- **B:** the *"ACME Bulk Access Request (Plugin)"* workflow (kept disabled, by design) and the *ACME Bulk Access Request* UI plugin.

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

## Screenshots
Real SailPoint screens (`docs/screenshots/`) from a test installation that used the prefix `UCSF`; yours show your own prefix:

| Launcher (A) | |
|---|---|
| ![Request the tool in the Request Center](docs/screenshots/launcher-0-request-center-access.png) | ![Launchpad](docs/screenshots/launcher-1-launchpad.png) |
| *Users request "Launcher Access" in the Request Center* | *…then launch it from the Launchpad* |
| ![The form](docs/screenshots/launcher-4-form-filled.png) | ![INC validation](docs/screenshots/launcher-3-inc-validation.png) |
| *People, items, one approver, INC, justification* | *A bad INC can't be submitted* |

| UI plugin (B), running inside SailPoint | |
|---|---|
| ![New request](docs/screenshots/plugin-in-isc-1-new-request.png) | ![My bulk requests](docs/screenshots/plugin-in-isc-2-my-bulk-requests.png) |
| *Four steps: people, access, approver and INC, review* | *Everything you've submitted, by INC* |

More plugin screens (made with sample data, one per step) are in [plugin/README.md](plugin/README.md) and [USAGE.md](USAGE.md).

## Folder layout
```
bulk-access-request/
├── README.md  INSTALL.md  USAGE.md        ← start here
├── bulkaccess.py                          ← the one command: show-config · apply · status · uninstall
├── config/bulk-access.example.json        ← copy to config/<tenant>.json (your copies stay out of git)
├── core/        shared Python package: config, API client, rules, definitions (+ tests)
├── launcher/    deployment A: install.py · status.py · uninstall.py · e2e.py
├── plugin/      deployment B: the Angular UI plugin + its install/status/uninstall
└── docs/        screenshots/ · dev/ (developer notes)
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
- **Unit tests:** core 215, plugin installer 12, plugin UI 156 (`pytest`, `ng test`), none of them needing a tenant.
