# Installing Bulk Access Request in any ISC tenant

Both deployments, **A (Launcher)** and **B (UI plugin)**, are set up from **one config file** with **one command**,
`bulkaccess.py`. This guide covers the shared setup and the Launcher in full. The plugin's own details (who can use it,
building, the dev server) are in [plugin/README.md](plugin/README.md).

## 1. Prerequisites
| You need | For | Why |
|---|---|---|
| **Python 3.10+** (nothing to `pip install`) | both | The installers use only the standard library. |
| A **Personal Access Token** for an **ORG_ADMIN** user, scope `sp:scopes:all` | both | Creates the form, workflow, launcher, access profile and plugin, and reads each catalog item's approval setup. Everything created is owned by that user (or by `owner`). |
| Workflows, **Forms** and **Launchers** available in the tenant | A | Standard in Identity Security Cloud. |
| **UI Plugins** turned on in the tenant | B | The plugin is a page inside ISC. |
| Node.js 22+ with **npm 11.12+**, and the SailPoint CLI `sail` 2.7+ | B, with `--deploy` | Builds and uploads the plugin. Install its dependencies once: `(cd plugin && npx -y npm@11 install)`. |
| Something in the **Request Center** catalog | both | The tool offers requestable access profiles, roles or entitlements. |
| **Request for others** allowed (Admin → Global → Access Request Settings) | B, `plugin.submit: "direct"` | The plugin files the access requests as the signed-in user, for other people. If the tenant doesn't allow it, the installer switches the plugin to `launcher` (with A installed) or stops with a message. |

Create the PAT under **Preferences → Personal Access Tokens → New Token** → *Other / No associated vendor
integration* → scope `sp:scopes:all`. Copy the secret straight away; it's shown only once.

## 2. Credentials
The installers read three environment variables. Put them in a `.env` file (and point `envFile` at it in your
config) or export them in your shell:
```bash
SAIL_BASE_URL=https://<tenant>.api.identitynow.com      # the *.api.* host
SAIL_CLIENT_ID=<PAT client id>
SAIL_CLIENT_SECRET=<PAT secret>
```
Keep that file private (`chmod 600 .env`) and out of git. `config/*.json` is already gitignored, apart from the example.
Credentials never go in the config file itself.

## 3. Configure
```bash
cp config/bulk-access.example.json config/<tenant>.json
python bulkaccess.py show-config --config config/<tenant>.json     # checks the file and shows what each route will use
```
This one file holds **every** setting for both deployments. The settings are grouped by what they control, not by
deployment, so the Launcher and the plugin behave the same way. Where SailPoint forces a difference (the Launcher's
30-person picker, no end date on the Launcher), the tool works it out for you; you never set a value twice.

`show-config` stops with a clear message if a value is invalid, and it tells you about old key names to update.

### Config reference
"Route" says who uses the setting: **A** = Launcher, **B** = UI plugin, **both**, or **CLI** = `bulkaccess.py` itself.

| Key | Default | Meaning | Route |
|---|---|---|---|
| `envFile` | none (example: `.env`) | Path to the `.env` file with the credentials above. Without it, they come from the environment. | both |
| `prefix` | required (example: `ACME`) | Starts the name of every object created, e.g. `ACME` → "ACME Bulk Access Request". At most 20 characters. | both |
| `mode` | `dry-run` | `dry-run`: the checks and emails run, but nothing is requested. `live`: the access is requested. | both |
| `deployments.launcher` | `true` | Install and manage deployment A. | CLI |
| `deployments.plugin` | `true` | Install and manage deployment B. At least one of the two must be `true`. | CLI |
| `inc.pattern` | `^INC\d{7}$` | The INC format, as a regular expression. Use one that means the same in Python and JavaScript (`\d`, `[A-Z]`, anchors, groups). | both |
| `inc.example` | `INC0012345` | The example shown in the field. Must match `inc.pattern`. | both |
| `inc.message` | "Enter a ServiceNow incident number, e.g. `<example>`." | The error shown for a bad INC. | both |
| `catalog.types` | all three | Which Request Center item types to offer: `ACCESS_PROFILE`, `ROLE`, `ENTITLEMENT`. Access profiles and roles are the ones the Request Center lists; entitlements are those marked **requestable** (SailPoint's catalog API doesn't list entitlements, so the tool reads requestable entitlements separately). | both |
| `catalog.nameStartsWith` | `null` | Only offer items whose names start with this text. `null` = all items. | both |
| `catalog.maxItems` | `25` | Most items per request, 1 to 25 (SailPoint's per-request limit). | both |
| `people.max` | `null` | Most people per request. `null` = no limit. The Launcher always stops at 30 (SailPoint's form limit), so it uses the smaller of this and 30. | both |
| `people.partSize` | `250` | People per Launcher run, 1 to 250 (SailPoint's workflow loop limit). A bigger plugin request sent through the Launcher goes out as several runs with the same INC. (Filed directly, the plugin always sends 10 people per access request, SailPoint's limit.) | B |
| `temporaryAccess.enabled` | `true` | Let requesters choose access that SailPoint removes automatically. `false` = permanent only. | both |
| `temporaryAccess.allow` | `["duration", "endDate"]` | How the end can be chosen: `duration` (a number and a unit) and/or `endDate` (a calendar date). The Launcher only offers `duration`. When the plugin submits through the Launcher, its end date is sent as a number of hours, so it needs `duration` allowed too. | both (`endDate`: B) |
| `temporaryAccess.units` | `HOURS`, `DAYS`, `WEEKS`, `MONTHS` | The duration units offered. | both |
| `temporaryAccess.maxDays` | `null` | Longest temporary access allowed, in days. `null` = no cap. A month counts as 31 days. | both |
| `notifications.overrideRecipients` | `[]` | Send **all** emails to these addresses instead of to the real people. Use this in test tenants. | A |
| `notifications.helpContact` | `null` | The emails' *Need help?* line, up to 300 characters; web and email addresses in it become links. `null` = "Contact your SailPoint administrator." | A |
| `notifications.uiBaseUrl` | `null` | Your tenant's UI address, for the links in the emails. `null` = derived from `SAIL_BASE_URL` (`https://acme.api.identitynow.com` → `https://acme.identitynow.com`). Set it if your users reach ISC at another address (a vanity domain). No query string, `#`, `$`, quotes or spaces. | A |
| `owner` | `null` | Owner identity ID for the objects created. `null` = the PAT user. **Recommended: a dedicated service identity.** The workflow owner is the *requester* of every access request the Launcher's workflow files, and ISC escalates to an admin any item approval that would go to the requester (e.g. when the owner also owns or manages an item). With `null`, `show-config` and the installers print a note saying so (not an error). | A |
| `access.launcherApproval` | `MANAGER` | Who approves requests for the *Launcher Access* profile: `MANAGER` (the requester's manager) or `NONE` (auto-approved). The old key `launcher.accessApproval` still works; `show-config` reminds you to rename it. | A |
| `plugin.alias` | `<prefix in lowercase>-bulk-access` | The plugin's alias: lowercase letters, digits and dashes. | B |
| `plugin.displayName` | `<prefix> Bulk Access Request` | The plugin's name in ISC. | B |
| `plugin.public` | `false` | `false`: the plugin is uploaded private (visible only to you). `true`: visible to everyone. | B |
| `plugin.submit` | `direct` | How the plugin submits. `direct`: it files the access requests itself, **as the signed-in user** (10 people per request), so they carry the requester's name and show in their Request Center; needs *Request for others* allowed in the tenant (the installer falls back to `launcher` if it isn't). `launcher`: it starts the Launcher (deployment A) and fills in its form as the signed-in user; the workflow then files the requests as its owner. Needs `deployments.launcher`. Either way, when A is installed only holders of the *Launcher Access* profile may submit. The old value `test-endpoint` now means `direct`, with a note. `show-config` says which applies. | B |

Keys named `_comment` are notes and are ignored.

**Settings that no longer do anything.** Bulk requests used to have one approver of their own. That approval is gone:
each item is approved the way SailPoint has it set up. `approval.*` (`timeoutDays`, `actionAtTimeout`, `priority`),
`notifications.ccApprover` and `notifications.pendingEmail` are still accepted, but ignored; `show-config` and `apply`
print a note for each, so you can delete them.

**Generated files are never edited by hand.** `apply` writes them from the config: the plugin's runtime config
`plugin/public/bulk-access.config.json` and manifest `plugin/sp-ui-plugin.json`, and, in the tenant, the workflows and
the Launcher's form. Change the config and run `apply` again instead.

## 4. Install
```bash
python bulkaccess.py apply --config config/<tenant>.json --dry-run              # prints every JSON body; changes nothing
python bulkaccess.py apply --config config/<tenant>.json --grant me --deploy     # installs both, gives *you* Launcher access, uploads the plugin
python bulkaccess.py status --config config/<tenant>.json                       # everything should say [ok]
```
`apply` runs every deployment that `deployments` turns on. The options:

| Option | What it does |
|---|---|
| `--dry-run` | Preview: prints what it would send. Nothing changes in the tenant or on disk. |
| `--only launcher` / `--only plugin` | Work on one deployment only. Also works with `status` and `uninstall`. |
| `--grant me` or `--grant <id>,<id>` | **A:** give these identities Launcher access right away, instead of having them request it. |
| `--deploy` | **B:** also build the plugin and upload it with `sail`. Without it, `apply` updates the workflow and the generated files only. |

`apply` is **idempotent**: run it again at any time, and always after you change the config. It finds its objects by
name and updates them.

**Deployment A creates:**
1. **Form** "<prefix> Bulk Access Request Form". Its item list is the Request Center catalog filtered by your config.
   When temporary access is on, it also asks for an access type (Permanent or Temporary), a duration and a unit.
   When the plugin sends a big request through the Launcher in parts, it also passes a `partLabel` (" (2/3)") with the
   form data, so each run's messages and email say which part it was. The form has no field for it (a hidden field
   makes the Launchpad show an empty form).
2. **Workflow** "<prefix> Bulk Access Request". It's enabled, and it only reacts to *its own* Launcher.
3. **Launcher** "<prefix> Bulk Access Request". Users see it in their **Launchpad**.
4. **Access profile** "<prefix> Bulk Access Request - Launcher Access". SailPoint only shows a Launcher to people
   who hold its `assignedLaunchers` entitlement, so this profile controls who can use the tool. People request it
   in the **Request Center**, approved by their manager if `access.launcherApproval` is `MANAGER` (auto-approved with
   `NONE`). Or grant it with `--grant`. About a minute after it's granted, the Launcher appears in their Launchpad.
   **The same profile lets them submit from the plugin.** Approvers need nothing: item approvers decide in
   **Approvals → Access Requests** or the plugin's Approvals tab.

**Deployment B** writes the plugin's runtime config and manifest, and creates nothing else in the tenant. The runtime
config carries the ID of A's *Launcher Access* profile (the page checks the signed-in user holds it before submitting),
the Launcher's ID with `plugin.submit: "launcher"` (people who aren't admins can't list launchers), and `noApproval`: the
catalog items SailPoint grants without any approval (access profiles and roles with no approval scheme; entitlements with
none of their own when the tenant's default for entitlements is none too). The page labels those items *"No approval:
granted right away"* and warns on the review step. So **install A first**; `apply` does, and with `launcher` it stops
with a clear message if the Launcher is missing. Without A there is no *Launcher Access* profile, and anyone who can open
the plugin can submit. With `--deploy` it builds the page and uploads the plugin, private unless `plugin.public` is
`true`. To put it in the menu: **Admin → Global → System Settings → Customize Navbar → Custom Item → Destination:
Plugin.** More in [plugin/README.md](plugin/README.md).

**When the catalog changes**, or an item's approval setup, run `apply` again (the plugin's `noApproval` list is
refreshed on every `apply`). To refresh only the Launcher form's choices, there's also
`python launcher/install.py --config config/<tenant>.json --sync-catalog`.

## 5. Verify end to end, before going live
`launcher/e2e.py` drives the whole Launcher flow through the API, as the requester:
```bash
python launcher/e2e.py --config config/<tenant>.json --people <testIdentityId>,<testIdentityId> \
    --items "<test item name>" --scenario request
#   --items "A,B"             → the form's items to request, by name (default: all of them; required in live mode)
#   --scenario bad-duration   → temporary access with an invalid duration: stopped before anything is requested
#   --access temporary --duration 1 --unit DAYS   → temporary access
#   --part 2/3                → also send the part label, as the plugin does for a big request
```
- **Who it runs as:** the PAT user, who needs Launcher access (`--grant me`).
- **What it does:** submits the form with a random INC and checks the workflow run: the requests are filed straight
  after the checks (there is no approval of the bulk request itself), and the requester is emailed.
- **In `live` mode**, it also checks that every person got one request per item whose comment carries the INC, the
  requester and the access, and (temporary access) a remove date of about now + the duration.
- **Use test identities and a harmless test item.** In `live` mode, runs really request access, and items without an
  approval of their own are granted at once. That's why a live `request` run needs `--items`: without it the form's
  whole catalog would be requested.
- **The safe sequence:** dry-run request → (`"mode": "live"`, `apply` again) live request on test identities → back to
  dry-run until you're ready.

For the plugin, submit a request from the page in `dry-run` mode: it checks everything and requests nothing. Then, in
`live` mode with test identities, try it as a user who isn't an admin but holds the *Launcher Access* profile: the access
requests carry their name and appear in their **Request Center → My Requests** and the plugin's **My bulk requests**;
someone without the profile is told to request it.

## 6. Go live
Set `"mode": "live"` (and clear `overrideRecipients`) in your config, then run `python bulkaccess.py apply` again.
`status` shows `installed mode=live`.

## 7. Uninstall
```bash
python bulkaccess.py uninstall --config config/<tenant>.json          # shows what it would delete
python bulkaccess.py uninstall --config config/<tenant>.json --yes    # deletes it
```
- It removes the objects of each deployment `deployments` turns on (or only `--only …`).
- It only deletes objects whose names this config produces. All of them start with the prefix.
- Access already granted *through* bulk requests stays in place: it's ordinary access now, and it shows up in
  certifications as usual. Temporary access still ends on its remove date.

## 8. Export the definitions as JSON
```bash
python bulkaccess.py export --config config/<tenant>.json             # as installed in the tenant (read-only)
python bulkaccess.py export --config config/<tenant>.json --offline   # as this code builds them, no tenant needed
```
- It writes `launcher/form.json`, `launcher/workflow.json`, `launcher/launcher.json` and `launcher/access-profile.json`
  (the plugin has no tenant definitions of its own).
- They go to `exports/<prefix>-<tenant|offline>/`, which git ignores, or to `--out DIR`.
- SailPoint's own fields (ids, created, modified, execution counts) are left out, so exports diff cleanly. A workflow file
  can be imported with **Admin → Workflows → Import**.
- Offline exports use placeholders such as `<FORM_DEFINITION_ID>` for IDs that only exist after install, and the form has
  no catalog items. Use them to review or diff what `apply` will create, not to install. `apply` remains the way to install.

## Advanced: the per-deployment scripts
`bulkaccess.py` calls these scripts for you. They still work on their own, with the same config file, if you need an
option that `bulkaccess.py` doesn't offer:

| Script | Extra options |
|---|---|
| `launcher/install.py`, `launcher/status.py`, `launcher/uninstall.py` | `install.py --sync-catalog` (only refresh the form's catalog choices) |
| `launcher/e2e.py` | the end-to-end test above |
| `plugin/install.py`, `plugin/status.py`, `plugin/uninstall.py` | `install.py --public` (overrides `plugin.public`), `--workdir <folder>` (build in another copy); `status.py --plugin` / `uninstall.py --plugin` (look up or delete the plugin instance) |

## How it works
```
Launchpad ─► Launcher ─► Workflow "<prefix> Bulk Access Request"
                           1. Interactive Form   people · items · access type and duration · INC · justification
                           2. checks             INC matches the pattern · duration is valid and within maxDays
                                                 (a failed check: Launchpad message + "Not requested" email; nothing is requested)
                           3. (live) Loop over people → Manage Access (all items for that person, with the remove duration)
                           4. Launchpad message + "Requested" email (dry-run: "Checked", nothing was requested)

UI plugin, plugin.submit "direct" (default)
          ─► check the user holds the Launcher Access profile
          ─► POST /access-requests/v1 as the signed-in user: up to 10 people per request, every item,
             each with the bulk comment and (temporary access) its removeDate; 3 requests at a time
UI plugin, plugin.submit "launcher"
          ─► per part (at most people.partSize people), as the signed-in user:
             launch the Launcher ─► find its form (interactive-process blocks) ─► fill in and submit it ─► steps 2–4 above
```
Every access request then goes through **its item's own approval** (owner, manager, …), as SailPoint has it set up;
items without one are granted at once. Filed directly, the requests carry the **signed-in user** as requester, so they
show in that person's Request Center and the plugin's **My bulk requests**. Filed by the Launcher's workflow (step 3),
the requester is the **workflow owner** (`owner`, or the installer's PAT user): item approvers see that account as
*Requested by*, and the requests are in its Request Center → My Requests, not the requester's.

Some of this design comes from limits we hit on a live tenant:
- **One request per person.** SailPoint caps a request at **10 recipients**, and **nested loops aren't allowed**.
  So the workflow loops over the people, and each request carries *all* the items.
- **At most 250 people per Launcher run.** The workflow loop refuses more than 250 items. Through the Launcher, the
  plugin splits a bigger list into parts, one run each, all with the same INC. The Launcher form can't hold more than 30
  anyway. Filed directly, the same 10-recipient cap means 10 people per request.
- **Temporary access uses SailPoint's own removal.** Manage Access takes a remove duration (hours, days, weeks or
  months), and SailPoint removes the access when it runs out. A workflow can't turn a date into a duration, so the
  Launcher offers durations only. The plugin sends an end date as the request's `removeDate` (direct), or converts it
  into hours (through the Launcher).
- **Catalog items are stored as full objects.** Each form choice holds the complete `{id, type, name}`, so the
  workflow never has to rebuild items from bare IDs. Run `apply` again when the catalog changes.
- **Inside a loop, steps only see `$.loop.*`.** So the workflow passes its whole state in as the loop context,
  which is how the INC and names reach every request's comment.
- **The requester never approves their own request.** If an item's approver is also its requester, ISC escalates that
  approval to an admin. Filed directly, that's the person who submitted; through the Launcher, the workflow owner
  (hence a dedicated service identity as `owner`).
- **The trigger is scoped to its own Launcher.** "Interactive process launched" fires for every Launcher, so the
  workflow filters on its own ID.
- **The installer re-enables the Launcher.** Disabling a workflow, which happens during updates, switches its
  Launcher off a moment later; the installer checks and turns it back on.
- **Emails are Velocity templates with their values in the context.** SailPoint's email service reads the body as a
  Velocity template, so a justification containing `#if(` would break an email written into it, and a multi-line one
  breaks a `{{…}}` value. The body is fixed markup and every value travels as a JSONPath context variable. Links point at
  your tenant's UI (`notifications.uiBaseUrl`, or derived from `SAIL_BASE_URL`); the plugin's page is looked up by alias
  at install time, so run `apply` once more after the plugin's first `--deploy` to add its links. USAGE.md section 6
  shows what each email says.

## Troubleshooting
| Symptom | Fix |
|---|---|
| `apply` or `show-config` stops with a config message | It names the key and the allowed values. Fix the config and run it again. |
| The Launcher isn't in someone's Launchpad | They need the *Launcher Access* profile. Request or grant it, wait about a minute, then refresh. |
| `HTTP 401 insufficient authorization` when launching | Same as above: the user doesn't hold the launcher's entitlement yet. |
| The plugin says "To submit, you need '<prefix> Bulk Access Request - Launcher Access'…" | Same: request it in the Request Center (or `--grant`), wait about a minute, and submit again. If they already hold it, the profile's entitlement was changed since (e.g. the Launcher was recreated): SailPoint reports holders correctly again after their next identity refresh. |
| The plugin's submit says SailPoint refused the request (HTTP 403) | The tenant doesn't let this user request access for others (*Request for others* in Access Request Settings), or their session expired. Allow it, or set `plugin.submit: "launcher"` and run `apply`. |
| The requester's Request Center doesn't show the item requests | Expected for Launcher requests: the workflow files them as its owner. They're in the owner's My Requests. Requests the plugin files directly are in the requester's own. |
| Everyone got an item at once, without approval | The item has no approval of its own in SailPoint. The plugin warns about these (`noApproval`); add an approval scheme to the item if it needs one, then run `apply`. |
| An item approval is titled "Modify: …" | The person already had the item; the request only changes its end date. |
| `HTTP 409 launcher is disabled` | Run `apply` again; it re-enables the Launcher. |
| The form offers nothing to request | Check `catalog.types` / `catalog.nameStartsWith`, then run `apply` again. An entitlement only appears once it is marked requestable. |
| "Temporary access isn't available." | `temporaryAccess.enabled` is `false`, or the chosen way (for example an end date on the Launcher) isn't allowed. |
| "Temporary access can last at most N days." | The duration is longer than `temporaryAccess.maxDays`. |
| An item approval went to an admin | Its approver was also the request's requester (often the workflow owner), so ISC escalated it. Use a dedicated service identity as `owner`. |
| HTTP 403 with "error code: 1010" from scripts | A proxy or Cloudflare is blocking the request. The client sends its own User-Agent; check `HTTPS_PROXY`. |
