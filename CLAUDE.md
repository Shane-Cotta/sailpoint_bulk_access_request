# Reassign Ownership on Termination: developer guide

An ISC workflow that, when an identity's lifecycle state becomes a termination state, moves the roles, access
profiles, entitlements, sources, apps, governance groups and identity profiles they own to their manager (or a
fallback owner). It ships with a stdlib-only Python installer
and tester. Human docs: `README.md`. The research behind the design, with sources: `docs/RESEARCH.md`.

This branch (`feature/ownership-reassignment`) is an orphan branch: it shares no history with `main` (the bulk access
request tool). `reassign/tenant.py` is a copy of main's `core/bulkaccess/tenant.py`.

## Layout
| Path | What |
|---|---|
| `ownership.py` | The CLI: `doctor`, `apply [--dry-run] [--enable]`, `test --departed … [--keep] [--no-objects] [--history]`, `status`, `uninstall [--yes]`, `export [--out]` |
| `reassign/workflow.py` | Pure builder: `build(cfg, Target)` → the workflow body. Step names, placeholders, `trigger_filter`, `http_step_names`, `ref_ids_of`, `test_input` |
| `reassign/config.py` | The only config loader (`config/<tenant>.json`, schema in `config/ownership.example.json`) |
| `reassign/ops.py` | Tenant calls: resolve identities, `find_parameter`, owned objects, workflow create/update, test endpoint, throwaway test objects |
| `reassign/tenant.py` | PAT client (`call`, `me`, `claims`) |
| `workflow/reassign-ownership-on-termination.json` | `export` output with placeholders: **generated, never edited by hand** (a test checks it's current) |

## Conventions
- **Nothing tenant-specific in code or the export.** Names come from the config `prefix`. IDs are resolved at install
  time. The export has only `__PLACEHOLDER__`s (a test rejects URLs and 32-hex IDs).
- **The workflow never holds a secret.** The HTTP steps are `sp:http` **v3** with a Parameter Storage reference
  (`param_authenticationRef: "oauth"`, `param_oauth{mapping, paramID, paramType "1.4", refID}`).
- **Install = POST, then PUT the same body** (see the facts below). `apply` reuses the installed refIDs on updates
  (`ref_ids_of`). A fresh install gets new UUIDs.
- New installs are **disabled**; `apply --enable` turns them on. `test` only runs a disabled workflow.
- `test` only ever touches objects it created (named `<prefix> Ownership Test …`). It refuses a "departed" identity who
  owns anything, because the workflow reassigns *everything* they own.
- Python changes: keep it stdlib-only (3.10+), and update `tests/test_workflow.py`. After a builder change, run
  `python ownership.py export` and commit the regenerated JSON.
- Tests: `python -m pytest -q -p no:cacheprovider tests` (no tenant needed).
- Never commit credentials. `.env`, `.env.*` and `config/*.json` (except the example) are gitignored.

## ISC behaviour this design depends on (verified live in a demo tenant, 2026-10-12)
- **Trigger** `idn:identity-lifecycle-state-changed`:
  - Input `{identity{id,name,type,email}, identityProfile, newLifecycleState, oldLifecycleState}`. It has no manager.
  - `filter.$` `$[?(@.newLifecycleState == "terminated")]` validated through `POST /beta/trigger-subscriptions/validate-filter`
    (`isValid` true for terminated, false for active).
  - The test endpoint (`POST /v2025/workflows/{id}/test`, disabled workflow, ORG_ADMIN) skips the filter and takes the
    input as given.
- **`sp:get-identity` v2:** the manager is `managerRef.id`, and the lifecycle state is `attributes.cloudLifecycleState`.
  - `IsPresent` on `$.getX.managerRef.id` takes the default branch when there's no manager. The choice entry has no `actionId`.
  - `StringMatches` on a missing path takes the default branch.
- **`sp:http` v2 with inline OAuth (do not use):**
  - Created through the API, the top-level steps' `oAuthClientSecret` was **dropped**, and the run failed ("Client
    Secret … not provided").
  - In steps **inside a loop** it was **stored in plaintext and returned by `GET /v2025/workflows/{id}`**.
  - New HTTP Request actions in the UI are v3 since March 2026.
- **`sp:http` v3 + Parameter Storage:**
  - A parameter is created in the UI (Admin → Global → Parameter Storage). Creating one through the API needs
    JWE-encrypted `privateFields` (undocumented).
  - `GET /v2025/parameter-storage/parameters` returns the public fields only (`type "1.4"`,
    `publicFields{clientId, tokenURL, credentialLocation}`).
  - **On `POST /v2025/workflows` ISC strips `paramID` and `refID`.** The run then fails: "failed to retrieve Parameters
    for authentication type 'oauth': unexpected Parameter configuration".
  - **A `PUT` with `paramID` and a client-generated `refID` (UUID) keeps both, and registers the reference.**
    `GET …/parameters/{id}/references` → `{consumerId "sp-workflow-config", usageHint {"workflowID": …}}`; one row per
    workflow, even with 21 steps.
  - Deleting the workflow removes its reference.
  - Uploading JSON in the UI (Create Workflow → Upload File) also strips the links, so each HTTP step has to be
    re-pointed in the builder. The uploaded graph passes the builder's validation.
- **Templating:**
  - `urlParams: 'filters:owner.id eq "{{$.trigger.identity.id}}"\nlimit:250'` works.
  - `jsonPatchRequestBody` with `"id": "{{$.loop.context.defineVariableOwner.newOwnerId}}"` works, and the PATCH
    changes the owner.
- **Owner lists:**
  - `owner.id eq` filters correctly on `/v3/roles`, `/v3/access-profiles`, `/v3/sources`, `/v2025/entitlements` and
    `/v2025/source-apps/all`. `owner.name` gives 400. Search has no sources index.
  - `/v2025/source-apps` (without `/all`) lists only the caller's *assigned* apps, so an app the PAT user merely
    created isn't in it. `/all` is the admin list. The v2025 path needs `X-SailPoint-Experimental: true` (`/beta` doesn't).
  - **`/v2025/workgroups` and `/v2025/identity-profiles` reject an `owner.id` filter (400).** The workflow lists them
    (`limit:250`) and filters with JSONPath: `$.getOwnedX.body[?(@.owner.id == $.trigger.identity.id)]`.
    - As a loop's `input.$`, this iterates over just the matches (8 groups, 8 iterations).
    - `IsPresent` on `…[?(…)].id` takes the default branch when nothing matches.
    - An empty loop input completes with no iterations.
  - `requestHeaders: "X-SailPoint-Experimental:true"` (one `Key:Value` per line) is sent by `sp:http` v3.
- **Owner PATCH:**
  - `[{"op":"replace","path":"/owner","value":{"type":"IDENTITY","id":…}}]` works on all seven types:
    `/v3/roles`, `/v3/access-profiles`, `/v3/sources`, `/v2025/entitlements`, `/v2025/workgroups`,
    `/v2025/identity-profiles`, `/v2025/source-apps` (with `X-SailPoint-Experimental: true`).
  - A nonexistent owner gives 400 (roles, access profiles, entitlements, identity profiles) or 404 (sources, apps).
  - **A governance group PATCHed to a nonexistent identity returns 2xx and clears its owner** (owner: null). That's why
    the workflow looks up the new owner (*Get New Owner*) before changing anything. `sp:get-identity` on an unknown ID
    fails the run with a 404, before any change.
- **Loops (`sp:loop:iterator`):**
  - A failing HTTP step fails **only that iteration**: the history shows `ChildWorkflowExecutionFailed` with the status.
  - The loop step itself completes, and the run continues. The Recheck steps then route to *Ownership not fully reassigned*.
  - An HTTP failure **outside** a loop fails the whole run immediately.
- **Failure end steps:**
  - They need top-level `failureName` / `description`.
  - The execution GET shows only `status: Failed`, and the history shows which "Stop - …" step ran.
- **Test objects:**
  - `POST /v3/sources {"connector": "delimited-file", owner, name, description, connectorAttributes}` creates a
    flat-file source.
  - To load an entitlement CSV, its `connectorAttributes` need `group.columnNames` (the group schema's 9 columns),
    `group.indexColumn` and `group.delimiter`. Without them the aggregation task fails ("Missing attribute columnNames").
  - `POST /v2025/sources/{id}/load-entitlements` (multipart, field `file`) needs a header with every group schema
    column, returns 202, and the entitlement appears in about 30 s.
  - The load also turns the header row into an entitlement named `displayName`, so pick yours by name. A new
    entitlement has `owner: null`.
  - `POST /v2025/workgroups {name, description, owner}`.
  - `POST /v2025/identity-profiles {name, owner, authoritativeSource, identityAttributeConfig}` works on the flat-file
    source.
  - `POST /v2025/source-apps {name, description, accountSource, matchAllAccounts}` ignores `owner`: the creator owns
    it, so the test PATCHes it.
  - Delete apps, identity profiles, governance groups, roles, access profiles, then the source; the entitlements go
    with the source.
- **Lookups:** `/v2025/identities?filters=alias eq "x" or name eq "x"` and `emailAddress eq` work.
  `/v2025/identities/{id}` returns 404 for a username.

Not verified: whether `/v3/roles` really returns more than 50 rows with `limit=250` (the spec says 50; the tenant has
13 roles). The Recheck step reports anything left over, so a lower cap shows up as *Ownership not fully reassigned*.
