# Installing Bulk Access Request in any ISC tenant

Both deployments, **A (Launcher)** and **B (UI plugin)**, are set up from **one config file** with **one command**,
`bulkaccess.py`. This guide covers the shared setup and the Launcher in full. The plugin's own details (who can use it,
building, the dev server) are in [plugin/README.md](plugin/README.md).

## 1. Prerequisites
| You need | For | Why |
|---|---|---|
| **Python 3.10+** (nothing to `pip install`) | both | The installers use only the standard library. |
| A **Personal Access Token** for an **ORG_ADMIN** user, scope `sp:scopes:all` | both | Creates the form, workflows, launcher, access profile and plugin. Everything created is owned by that user (or by `owner`). |
| Workflows, **Forms** and **Launchers** available in the tenant | A | Standard in Identity Security Cloud. |
| **UI Plugins** turned on in the tenant | B | The plugin is a page inside ISC. |
| Node.js 22+ with **npm 11.12+**, and the SailPoint CLI `sail` 2.7+ | B, with `--deploy` | Builds and uploads the plugin. Install its dependencies once: `(cd plugin && npx -y npm@11 install)`. |
| Something in the **Request Center** catalog | both | The tool offers requestable access profiles, roles or entitlements. |

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
| `mode` | `dry-run` | `dry-run`: approvals and emails run, but nothing is requested. `live`: approved requests are submitted. | both |
| `deployments.launcher` | `true` | Install and manage deployment A. | CLI |
| `deployments.plugin` | `true` | Install and manage deployment B. At least one of the two must be `true`. | CLI |
| `inc.pattern` | `^INC\d{7}$` | The INC format, as a regular expression. Use one that means the same in Python and JavaScript (`\d`, `[A-Z]`, anchors, groups). | both |
| `inc.example` | `INC0012345` | The example shown in the field. Must match `inc.pattern`. | both |
| `inc.message` | "Enter a ServiceNow incident number, e.g. `<example>`." | The error shown for a bad INC. | both |
| `catalog.types` | all three | Which Request Center item types to offer: `ACCESS_PROFILE`, `ROLE`, `ENTITLEMENT`. | both |
| `catalog.nameStartsWith` | `null` | Only offer items whose names start with this text. `null` = all items. | both |
| `catalog.maxItems` | `25` | Most items per request, 1 to 25 (SailPoint's per-request limit). | both |
| `people.max` | `null` | Most people per request. `null` = no limit. The Launcher always stops at 30 (SailPoint's form limit), so it uses the smaller of this and 30. | both |
| `people.partSize` | `250` | People per approval, 1 to 250 (SailPoint's workflow loop limit). A bigger plugin request is sent as several approvals with the same INC. | B |
| `temporaryAccess.enabled` | `true` | Let requesters choose access that SailPoint removes automatically. `false` = permanent only. | both |
| `temporaryAccess.allow` | `["duration", "endDate"]` | How the end can be chosen: `duration` (a number and a unit) and/or `endDate` (a calendar date). The Launcher only offers `duration`. | both (`endDate`: B) |
| `temporaryAccess.units` | `HOURS`, `DAYS`, `WEEKS`, `MONTHS` | The duration units offered. | both |
| `temporaryAccess.maxDays` | `null` | Longest temporary access allowed, in days. `null` = no cap. A month counts as 31 days. | both |
| `approval.timeoutDays` | `7` | Days before the approval task times out, 1 to 90. | both |
| `approval.actionAtTimeout` | `EXPIRED` | What happens at the timeout: `EXPIRED` (nothing is requested) or `APPROVED`. | both |
| `approval.priority` | `MEDIUM` | The approval task's priority: `LOW`, `MEDIUM` or `HIGH`. | both |
| `notifications.overrideRecipients` | `[]` | Send **all** emails to these addresses instead of to the real people. Use this in test tenants. | both |
| `notifications.ccApprover` | `true` | Copy the approver on the outcome email. | both |
| `owner` | `null` | Owner identity ID for the objects created. `null` = the PAT user. | both |
| `access.launcherApproval` | `MANAGER` | Who approves requests for the *Launcher Access* profile: `MANAGER` (the requester's manager) or `NONE` (auto-approved). The old key `launcher.accessApproval` still works; `show-config` reminds you to rename it. | A |
| `plugin.alias` | `<prefix in lowercase>-bulk-access` | The plugin's alias: lowercase letters, digits and dashes. | B |
| `plugin.displayName` | `<prefix> Bulk Access Request` | The plugin's name in ISC. | B |
| `plugin.public` | `false` | `false`: the plugin is uploaded private (visible only to you). `true`: visible to everyone. | B |

Keys named `_comment` are notes and are ignored.

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
2. **Workflow** "<prefix> Bulk Access Request". It's enabled, and it only reacts to *its own* Launcher.
3. **Launcher** "<prefix> Bulk Access Request". Users see it in their **Launchpad**.
4. **Access profile** "<prefix> Bulk Access Request - Launcher Access". SailPoint only shows a Launcher to people
   who hold its `assignedLaunchers` entitlement, so this profile controls who can use the tool. People request it
   in the **Request Center**, approved by their manager if `access.launcherApproval` is `MANAGER`. Or grant it with `--grant`.

**Deployment B creates** the workflow "<prefix> Bulk Access Request (Plugin)" (disabled, by design) and writes the
plugin's runtime config and manifest. With `--deploy` it builds the page and uploads the plugin, private unless
`plugin.public` is `true`. To put it in the menu: **Admin → Global → System Settings → Customize Navbar → Custom Item →
Destination: Plugin.** More in [plugin/README.md](plugin/README.md).

**When the catalog changes**, run `apply` again. To refresh only the Launcher form's choices, there's also
`python launcher/install.py --config config/<tenant>.json --sync-catalog`.

## 5. Verify end to end, before going live
`launcher/e2e.py` drives the whole Launcher flow through the API, playing both the requester and the approver:
```bash
python launcher/e2e.py --config config/<tenant>.json \
    --people <testIdentityId>,<testIdentityId> --approver <otherTestIdentityId> --scenario approve
#   --scenario deny   → the approver says no, so nothing is requested
#   --scenario self   → the requester names themselves; the run stops before any approval exists
```
- **Who it runs as:** the PAT user, who needs Launcher access (`--grant me`).
- **What it does:** submits the form with a random INC, checks that **one** approval went to `--approver`,
  approves or rejects it on their behalf (ORG_ADMIN), and checks the result.
- **In `live` mode**, it also checks that every person got a request whose comment carries the INC.
- **Use test identities and a harmless test item.** In `live` mode, approved runs really request access.
- **The safe sequence:** dry-run approve → dry-run deny → (`"mode": "live"`, `apply` again) live approve on test
  identities → back to dry-run until you're ready.

For the plugin, submit a request from the page in `dry-run` mode and approve it in **Home → Approvals**.

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
- It writes `launcher/form.json`, `launcher/workflow.json`, `launcher/launcher.json`, `launcher/access-profile.json` and
  `plugin/workflow.json` (only for the deployments that are switched on, or `--only …`).
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
                           1. Interactive Form   people · items · access type and duration · approver · INC · justification
                           2. checks             approver ≠ requester · INC matches the pattern · duration is valid
                           3. Generic Approval   ONE task, assigned to the chosen approver
                           4. if APPROVED (live) Loop over people → Manage Access (all items for that person, with the remove duration)
                           5. email              approved / denied (dry-run says nothing was requested)

UI plugin ─► one workflow test run per part (at most people.partSize people each) ─► the same steps 2–5
```
Some of this design comes from limits we hit on a live tenant:
- **One request per person.** SailPoint caps a request at **10 recipients**, and **nested loops aren't allowed**.
  So the workflow loops over the people, and each request carries *all* the items.
- **At most 250 people per approval.** The workflow loop refuses more than 250 items. The plugin splits a bigger list
  into parts, each with its own approval, all with the same INC. The Launcher form can't hold more than 30 anyway.
- **Temporary access uses SailPoint's own removal.** Manage Access takes a remove duration (hours, days, weeks or
  months), and SailPoint removes the access when it runs out. A workflow can't turn a date into a duration, so the
  Launcher offers durations only; the plugin converts an end date into hours itself.
- **Catalog items are stored as full objects.** Each form choice holds the complete `{id, type, name}`, so the
  workflow never has to rebuild items from bare IDs. Run `apply` again when the catalog changes.
- **Inside a loop, steps only see `$.loop.*`.** So the workflow passes its whole state in as the loop context,
  which is how the INC and names reach every request's comment.
- **Self-approval is blocked up front.** SailPoint reassigns a self-approval to some other admin.
- **The trigger is scoped to its own Launcher.** "Interactive process launched" fires for every Launcher, so the
  workflow filters on its own ID.
- **The installer re-enables the Launcher.** Disabling a workflow, which happens during updates, switches its
  Launcher off a moment later; the installer checks and turns it back on.

## Troubleshooting
| Symptom | Fix |
|---|---|
| `apply` or `show-config` stops with a config message | It names the key and the allowed values. Fix the config and run it again. |
| The Launcher isn't in someone's Launchpad | They need the *Launcher Access* profile. Request or grant it, wait about a minute, then refresh. |
| `HTTP 401 insufficient authorization` when launching | Same as above: the user doesn't hold the launcher's entitlement yet. |
| `HTTP 409 launcher is disabled` | Run `apply` again; it re-enables the Launcher. |
| The form offers nothing to request | Check `catalog.types` / `catalog.nameStartsWith`, then run `apply` again. |
| "Temporary access isn't available." | `temporaryAccess.enabled` is `false`, or the chosen way (for example an end date on the Launcher) isn't allowed. |
| "Temporary access can last at most N days." | The duration is longer than `temporaryAccess.maxDays`. |
| An approval went to someone unexpected | The requester chose themselves. That's now blocked; check the workflow is up to date (`status`). |
| HTTP 403 with "error code: 1010" from scripts | A proxy or Cloudflare is blocking the request. The client sends its own User-Agent; check `HTTPS_PROXY`. |
