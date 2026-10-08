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
| `plugin_temporary_modes` / `launcher_temporary_modes` | derived | the Launcher drops `endDate` |
| `deploy_launcher`, `deploy_plugin`, `deployments` | `deployments.*` | at least one must be true |
| `plugin_public` | `plugin.public` | default false; the installer pushes `--private` unless this or `--public` is set |
| `launcher_access_approval` | `access.launcherApproval` | the old `launcher.accessApproval` still works (it adds a note to `cfg.deprecations`) |

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

## 3. Plugin workflow input (one workflow-test run **per part**)
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
- **Approval name:** `Bulk access {inc}{partLabel}` (≤ 50 characters). The Launcher is always one part, so `partLabel` is empty there.
- **Approval description:** `{prefix} bulk access request {inc}{partLabel} from {requester} · {accessLabel}`.
- **Manage Access:** `versionNumber: 2`, inside the existing loop, `removeDuration` from the input (plugin:
  `removeDuration.$: "$.loop.context.trigger.removeDuration"`; the Launcher builds it from its form fields).
- **Item comment:** `{inc} | Bulk access request by {requester} | Approved by {approver} | {accessLabel} | {justification}`. The
  INC stays first, because the plugin's My bulk requests reads it from there.
- **Emails** (approved and denied) say which part (when parts > 1) and the access label.
- **Launcher form, new fields** (keys): `accessType` (Permanent or Temporary, default Permanent), `duration` (a whole number, only used
  when Temporary), `durationUnit` (from `temporary_units`). Only offered when `launcher_temporary_modes` contains `duration`. The
  Launcher's access label can be `Temporary: {n}{suffix}` (e.g. `Temporary: 30d`) if the unit word can't be templated.
  The Launcher must enforce the same rules (whole number ≥ 1, `maxDays`) before the approval, with a clear Launchpad message.

## 5. Plugin runtime config (`public/bulk-access.config.json`, written by `plugin/pluginlib.py`)
New fields (the rest are unchanged): `"peopleMax": null | number`, `"partSize": number`,
`"temporary": {"enabled": bool, "allow": ["duration","endDate"], "units": ["HOURS",...], "maxDays": null | number}`.
`runtime-config.ts` validates: peopleMax null or ≥ 1; partSize 1..250; allow/units from the fixed lists.

## 6. One CLI (`bulkaccess.py` at the repo root)
```
python bulkaccess.py show-config --config config/<tenant>.json
python bulkaccess.py apply       --config … [--dry-run] [--only launcher|plugin] [--deploy] [--grant me|<ids>]
python bulkaccess.py status      --config … [--only …]
python bulkaccess.py uninstall   --config … [--only …] [--yes]
```
It runs each enabled deployment by calling the existing `launcher/*.py` and `plugin/*.py` `main(argv)` functions. They're loaded
by file path, because both folders have an `install.py`. Those scripts keep working on their own. `plugin/install.py` keeps its flags
and takes its `--public` default from `cfg.plugin_public`.

## Test data and safety (live tests in a test or shared tenant)
- Only touch objects named with the config's prefix. Use a harmless test access profile as the only catalog item
  (`catalog.nameStartsWith`), test identities as the people, and an approver who isn't the PAT user (the requester).
- Each live grant uses a fresh test identity; the brief lists which ones.
- **Every approval a test creates must be decided** (approve or reject via the API) or cancelled. Never leave one pending.
- Leave every installation in `mode: dry-run` when you finish.
- No secrets in files or output. Never `sail --debug`. No `sed -i` (it breaks file permissions on this mount).
