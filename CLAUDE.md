# Bulk Access Request — developer guide

Bulk access requests for SailPoint Identity Security Cloud (ISC): many people × many Request Center items, approved by
**one** chosen approver, tracked by a **ServiceNow INC number**. Two independently installable deployments share one core and one config file.
Human docs: `README.md` (overview), `INSTALL.md` (any tenant), `USAGE.md` (requesters, approvers, admins), `plugin/README.md`.

## Layout
| Path | What |
|---|---|
| `bulkaccess.py` | The one CLI: `show-config`, `apply [--dry-run] [--only launcher\|plugin] [--deploy] [--grant me\|<ids>]`, `status`, `uninstall [--yes]`. It calls each enabled deployment's `main(argv)`, loaded by file path (both folders have an `install.py`). |
| `core/bulkaccess/` | Shared Python (standard library only): `config.py` (the only config loader, plus derived per-route values), `tenant.py` (PAT client), `rules.py` (validation, parts, temporary access, reading the bulk item comment), `definitions.py` (pure JSON builders for the form, workflows and launcher) |
| `launcher/` | Deployment A: `install.py`, `status.py`, `uninstall.py`, `e2e.py` (still runnable on their own) |
| `plugin/` | Deployment B: Angular + PrimeNG UI plugin, plus `install.py` / `status.py` / `uninstall.py` / `pluginlib.py` |
| `config/` | `bulk-access.example.json` (committed; the schema). Per-tenant `config/<tenant>.json` files are **gitignored**. |
| `docs/dev/CONTRACTS.md` | The shared spec for parts, temporary access and the central config: workflow input/output, exact rule messages, live-verified facts |
| `docs/screenshots/` | Images used by the docs |
| `.claude/agents/` | `bulk-access-builder.md`: the agent definition for parallel workstreams (one worktree and branch each) |

## Conventions
- **One config.** Every setting for both deployments lives in `config/<tenant>.json`, grouped by concern, not by deployment.
  `core/bulkaccess/config.py` is the only loader. Route-specific values are **derived there** (`launcher_people_cap`,
  `plugin_people_max`, `launcher_temporary_modes`, `plugin_temporary_modes`, …); never re-implement them in an installer.
  Renamed keys stay readable and add a note to `cfg.deprecations` (e.g. `launcher.accessApproval` → `access.launcherApproval`).
- **Generated files are never edited by hand:** `plugin/public/bulk-access.config.json`, `plugin/sp-ui-plugin.json`, and the
  workflows and form in the tenant all come from the config.
- **No tenant-specific anything in code.** Names come from the config `prefix`, IDs are looked up by name at install time.
  Example values use the placeholder prefix `ACME`.
- **New installs default to `mode: dry-run`**; `live` actually requests access. Test with test identities and a harmless
  test access profile, and set `notifications.overrideRecipients` in shared or test tenants.
- **Never commit credentials.** The PAT comes from `SAIL_BASE_URL` / `SAIL_CLIENT_ID` / `SAIL_CLIENT_SECRET` (env or the config's `envFile`).
- Python changes: keep `core` dependency-free and pure where possible; update `core/tests/test_core.py`.
- The plugin's `src/app/bulk/rules.ts` mirrors `core/bulkaccess/rules.py`; change both together (their specs mirror each other).
  Problem messages must match **exactly** in both languages (see `docs/dev/CONTRACTS.md` §2).
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
- **Generic Approval:**
  - `approvalType SINGLE`, `singleApproverCategory IDENTITY`, `singleApproverIdentityId.$`. Branch on `$.<step>.status` (`APPROVED`).
  - A self-approval is silently reassigned to some admin, so it's blocked up front.
  - An ORG_ADMIN can approve or reject on someone's behalf (`/v2025/generic-approvals/{id}/approve|reject`). That's recorded as a manual reassignment: the original approver is the first `reassignmentHistory[].reassignedFrom`.
  - `/v2025/generic-approvals` also returns access-request approvals, as `type: ACCESS_REQUEST_APPROVAL` (with `requestee`,
    `requestedTarget`, `assignedTo`, `approvalConfig.serialChain`, `referenceData`). Their `comments[]` (where our INC is) only
    come back with `include-comments=true`, and `assignedTo` is then left out. Comment text can't be searched or filtered, so group by INC in the page.
  - `mine=true` returns only the caller's approvals. For a non-admin the plain list is already theirs, and `approverId=<someone else>` returns 400.
  - `generic-approvals/bulk-approve` and `bulk-reject` take at most 50 IDs and return `202 {}` even for unknown IDs, so re-read
    to confirm. **A non-admin gets 403**, even for their own approvals.
  - **Deciding as a non-admin:**
    - Single `/{id}/approve|reject` works on the caller's own approvals and is recorded as theirs (`approvedBy`/`rejectedBy`,
      with `actionedAs`, e.g. `ACCESS_PROFILE_OWNER`).
    - On someone else's approval it returns 403.
    - Reject returns 200 with the approval (the spec says 204).
  - The list leaves `approvedBy`/`rejectedBy` empty; only the single GET has them.
  - Item approval schemes still apply to requests a workflow submits. The requester is the workflow owner, and if that identity
    is also the approver, ISC escalates the approval to an admin.
- **Requesting access from a workflow:**
  - `sp:create-approval-request` breaks on one-item lists (the engine unwraps single-element arrays); use `sp:access:manage`.
  - A request takes at most 10 recipients, and nested loops are rejected, so the workflow loops over people and each request carries all the items.
  - Steps inside a loop only see `$.loop.*`, so the loop gets `context.$: "$"` and reads `$.loop.context.…`.
  - **Item approval schemes still apply** to the requests the workflow submits: an access profile with an OWNER scheme gets
    one approval per person for its owner, after the bulk approval. (The plugin's Approvals tab exists for those approvers.)
  - The **requester** of those requests is always the **workflow owner** (the PAT user), not whoever filled in the form. If the
    workflow owner is also an item's approver, ISC escalates that approval to an admin ("…because the Identity X is the Requester").
  - **Is X in a list?** `StringContains` doesn't search a list. Use a JSONPath filter, `$.list[?(@ == $.getApprover.id)]`, with
    `StringEquals` against the same value. Compare with a plain string: the Launcher form's one-item approver list never matches.
  - **Loop (`sp:loop:iterator`) has a hard 250-item limit:** above it the step fails ("Input has N iterations which exceed 250
    iteration limit"). 250 runs in about 13 s, in parallel. So one workflow run (one approval) covers at most 250 people; the plugin sends parts.
  - **Serial Loop (`sp:serial:iterator`) is not usable:** it silently stops after 50 items with no error (and at the first failing item).
- **Temporary access (Manage Access `versionNumber: 2`):**
  - v2 has `removeDuration` (and `startDate`). v1 only had days and weeks, and not for entitlements. Temporary entitlements work in v2.
  - `removeDuration` is a string `"<n><suffix>"`: `"2h"`, `"1d"`, `"1w"` (+7 days), `"1M"` (+1 month).
  - Missing (a JSONPath to a missing key) or `""` = permanent (`removeDate: null`), so always pass it; no branching needed.
  - An invalid value (e.g. `"abc"`) **fails the step** ("timeext: invalid duration"). Only send validated strings.
  - No workflow transform turns a date into a duration, so the Launcher offers durations only; the plugin converts an end date to hours itself.
- **`/v3/access-request-status` rows:** `requestedFor` is an object `{id,name,type}`, `name` is the item name, `removeDate` holds the
  expiry, and `requesterComment.comment` holds our comment. There's no `requestedObject`.
- **Workflow definitions:** failure end steps need top-level `failureName` / `description`. Launcher-triggered workflows must filter
  `$[?(@.workflowId == '<own id>')]`.
- **Variables and operators:**
  - "Update Variable" only accepts variables from a step whose name starts with "Define Variable".
  - A variable can't be defined as a literal `""`, but a replace transform that empties a placeholder works. Concatenation fails on numbers.
  - `sp:compare-boolean` treats the string `"true"` as false.
- **Launchers:**
  - Visible and launchable only for holders of the auto-created `assignedLaunchers` entitlement (on the IdentityNow source). The installer wraps it in a requestable "Launcher Access" profile.
  - Disabling the workflow disables its Launcher a moment later.
- **Plugins:** a browser plugin can't hold a workflow's external-trigger secret, so the plugin uses the workflow **test** endpoint. That
  requires a disabled workflow and an ORG_ADMIN user.
- **APIs:**
  - `/v3/requestable-objects` needs `types=` repeated; a comma list containing ENTITLEMENT returns 400.
  - There's no v3 identities API; use `/v2025/identities`.
  - Some tenants sit behind Cloudflare, which rejects Python's default User-Agent; the client sends its own.
