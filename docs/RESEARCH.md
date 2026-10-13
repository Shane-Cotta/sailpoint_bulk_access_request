# Research: SailPoint ISC APIs and object ownership

> **Paths:** this research used the `v3`/`v2025` paths of the time. Since 2026-10-13 the tool calls their GA
> `/<service>/v1` equivalents (`reassign/api.py`, e.g. `/roles/v1`, `/workgroups/v1`), re-checked live with the same
> filters and shapes.

The facts behind this workflow's design, gathered 2026-10-12 from SailPoint's documentation, the published OpenAPI
specs, the developer community, and a live demo tenant. **Live** means checked against the tenant (the details are in
`CLAUDE.md`). **Docs** means from SailPoint's documentation or specs, without a live check.

## 1. Who can own what

| Object | Owner field | Allowed primary-owner type | Find what someone owns | Change the owner |
|---|---|---|---|---|
| Role | `owner {type,id,name}` | IDENTITY only | `GET /v3/roles?filters=owner.id eq "<id>"` (**live**) | `PATCH /v3/roles/{id}`, JSON Patch `replace /owner` (**live**) |
| Access profile | `owner {type,id,name}` | IDENTITY only | `GET /v3/access-profiles?filters=owner.id eq "<id>"` (**live**) | `PATCH /v3/access-profiles/{id}` (**live**) |
| Source | `owner {type,id,name}`; `managementWorkgroup` for a governance group | IDENTITY only | `GET /v3/sources?filters=owner.id eq "<id>"` (**live**) | `PATCH /v3/sources/{id}`: patch `/owner` only, never PUT a source (**live**) |
| Entitlement | `owner {type,id,name}` (often null) | IDENTITY only | `GET /v2025/entitlements?filters=owner.id eq "<id>"` (**live**) | `PATCH /v2025/entitlements/{id}` (**live**) |
| App (source app) | `owner {type,id,name}` | IDENTITY | `GET /v2025/source-apps/all?filters=owner.id eq "<id>"`, with `X-SailPoint-Experimental: true` (**live**). Plain `/source-apps` lists only the caller's assigned apps | `PATCH /v2025/source-apps/{id}` + the header (**live**) |
| Governance group | `owner {type,id,name,displayName,emailAddress}` | IDENTITY | **No owner filter** (`owner.id` → 400): list `GET /v2025/workgroups` and compare `owner.id` (**live**) | `PATCH /v2025/workgroups/{id}` (**live**). An unknown identity ID **clears** the owner instead of failing (**live**) |
| Identity profile | `owner {type,id,name}` | IDENTITY | **No owner filter** (400): list `GET /v2025/identity-profiles` and compare (**live**) | `PATCH /v2025/identity-profiles/{id}` (**live**) |
| Workflow | `owner` | IDENTITY, which must be an admin | `GET /v2025/workflows` | `PATCH /v2025/workflows/{id}` (docs; not handled by this workflow) |

- **The owner value is** `{"type": "IDENTITY", "id": "<id>"}`. Leave `name` out: if it doesn't match the identity's
  current name, the call returns 400 (`OwnerReference` schema, docs).
- **A governance group can't be the primary owner** of roles, access profiles, sources or entitlements (400). It can
  only be an *additional* owner (`additionalOwners`) or a source's `managementWorkgroup` (docs). So the fallback owner
  is an identity.
- **Filtering:** where it's supported, `owner.id` takes `eq` and `in`. `owner.name` is not filterable: 400
  "Properties are not queryable" (**live**).
- **Governance groups and identity profiles** can't be filtered by owner, so the workflow lists the first 250 and
  picks the departed identity's with a JSONPath filter, `body[?(@.owner.id == $.trigger.identity.id)]` (**live**).
- **Search:** `POST /v3/search` on the `roles` / `accessprofiles` indices with `owner.id:<id>` works (**live**). But
  there is **no sources index** (400, **live**), and the index lags behind changes. The identities index has an `owns`
  object (`sources`, `roles`, `accessProfiles`, `entitlements`, `apps`, `governanceGroups`, `fallbackApprover`). It's
  useful for reporting, but too stale to drive changes. The workflow therefore uses the list APIs.
- **Page sizes:** list endpoints take `limit` up to 250. The roles spec says 50 (not verified beyond 13 roles).
  Workflow loops take at most 250 items.

## 2. The trigger
- **`idn:identity-lifecycle-state-changed`** ("Identity Lifecycle State Changed") sends
  `{identity{id,name,type,email}, identityProfile, newLifecycleState, oldLifecycleState}` (**live**: the action
  library's input example and output schema).
- The states are **technical names**, and the filter is case-sensitive.
- Filter: `$[?(@.newLifecycleState == "terminated")]` (**live**, through `POST /beta/trigger-subscriptions/validate-filter`).
- **`idn:identity-lifecycle-state-change-processed`** fires after the state's configured actions have run, and its
  payload includes `manager{id,name,email}` (**live**, schema). It isn't used here because the requirement names the
  *Changed* trigger, and Get Identity gives the manager anyway.
- The older approach is *Identity Attributes Changed* with
  `$.changes[?(@.attribute == "cloudLifecycleState" && @.newValue == "terminated")]`.
- Sources: [workflow triggers](https://documentation.sailpoint.com/saas/help/workflows/workflow-triggers.html),
  [lifecycle triggers announcement](https://developer.sailpoint.com/discuss/t/new-capability-workflow-triggers-for-lifecycle-state-changes/149112),
  [filtering events](https://developer.sailpoint.com/docs/extensibility/event-triggers/filtering-events/),
  [filter thread](https://developer.sailpoint.com/discuss/t/jsonpath-filter-for-trigger-lifecycle-state-change/184441).

## 3. Workflow actions used
- **Get Identity** (`sp:get-identity` v2): `managerRef{id,name}` (missing when there's no manager) and
  `attributes.cloudLifecycleState` (**live**).
- **HTTP Request** (`sp:http`):
  - Non-2xx responses fail the step. Inside a loop, that fails only the iteration (**live**).
  - **v2** keeps credentials inline (`authenticationType: "OAuth"`, `oAuthClientSecret`, …). Through the API, the secret
    was dropped from top-level steps and stored readable inside loops (**live**). Don't use it for new work.
  - **v3** (since March 2026) takes credentials from **Parameter Storage**: `param_authenticationRef: "oauth"`,
    `param_oauth{mapping, paramID, paramType "1.4", refID}`. A workflow keeps `paramID` only on update (PUT) and with a
    `refID` (**live**).
  - Content types include `json-patch+json` (body in `jsonPatchRequestBody`). `urlParams` takes one `key:value` per line.
  - Sources: [HTTP Request with Parameter Storage](https://developer.sailpoint.com/discuss/t/enhancement-http-request-action-with-parameter-storage-credentials/199002),
    [refID via API](https://developer.sailpoint.com/discuss/t/how-to-set-refid-for-http-action-in-workflow-via-api/206897),
    [creating type 1.4 parameters via API (JWE)](https://developer.sailpoint.com/discuss/t/parameter-storage-create-parameter-type-1-4-returns-generic-validation-error-when-using-jwe-privatefields/205998),
    [Parameter Storage](https://documentation.sailpoint.com/saas/help/parameter_storage/index.html),
    [managing parameters](https://documentation.sailpoint.com/saas/help/parameter_storage/managing_parameters.html),
    [JSON Patch support](https://developer.sailpoint.com/discuss/t/workflows-http-request-now-supports-json-patch-content-type/10269),
    [HTTP failure handling](https://developer.sailpoint.com/discuss/t/handling-failure-response-codes-in-workflow/72861).
- **Loop** (`sp:loop:iterator`):
  - Runs in parallel, with at most 250 items, and loops can't be nested.
  - Inside it only `$.loop.*` exists, so the run is passed in with `context.$: "$"`.
  - The serial loop (`sp:serial:iterator`) allows up to 1,000 items (50 in test mode), but stops at the first
    failure, so it isn't used.
  - Source: [workflow actions](https://documentation.sailpoint.com/saas/help/workflows/workflow-actions.html),
    [1k serial loops](https://developer.sailpoint.com/discuss/t/new-capability-workflows-with-1k-serial-loops/207478).
- **Define Variable / Update Variable:**
  - Update only works on variables from a step named "Define Variable…".
  - Verify Data Type (`IsPresent`) and Compare Strings (`StringEquals`, `StringMatches`) route the choices.
  - Source: [workflow operators](https://documentation.sailpoint.com/saas/help/workflows/workflow-operators.html).
- **There is no native "change owner" action.** Get Access, Manage Access and the like work on access, not
  ownership (**live**: the action library has nothing for owners), so ownership changes go through HTTP Request.

## 4. Permissions
- The PAT acts with its user's levels. **ORG_ADMIN** covers everything here: reading and patching roles, access
  profiles and sources, and running the test endpoint.
- Narrower levels exist (ROLE_ADMIN/ROLE_SUBADMIN for roles, SOURCE_ADMIN/SOURCE_SUBADMIN for access profiles and
  sources), but subadmins are limited to their own segments or sources (docs).
- Scopes if you restrict the PAT: `idn:role-unchecked:manage` (or `-checked`), `idn:access-profile:manage`,
  `idn:sources:manage` and the matching read scopes (docs).

## 5. How others solve it
- **ISC Governance connector**, reassignment settings (SailPoint's recommendation): when the identity's governance
  account is disabled, it reassigns owned objects and pending work to the manager. It requires a manager, and has no
  documented fallback.
  [Docs](https://documentation.sailpoint.com/connectors/saas/identity_security_cloud_gov/help/saas_connectivity/identity_security_cloud_governance/reassignment_settings.html),
  [CoLab predecessor](https://github.com/sailpoint-oss/colab-saas-conn-identitynow-management).
- **`sail reassign`** (SailPoint CLI): one-off moves of sources, roles, access profiles, entitlements, identity
  profiles, governance groups and workflows, with `--dry-run`.
  [Docs](https://developer.sailpoint.com/docs/tools/cli/reassign/).
- **Community workflow pattern**, "Reassign Object Owners Using IdentityNow Workflows":
  - On termination, a form asks the manager who should take over, then child workflows run per object type.
  - Each list uses `limit=1`, a PATCH, and the workflow calling itself until nothing is left. That avoids loops and the
    search index.
  - [Post](https://developer.sailpoint.com/discuss/t/reassign-object-owners-using-identitynow-workflows/22371).
- This design keeps one workflow with one loop per object type (250 per type per run), a check that the new owner
  exists and is active, and a final check. That trades unlimited volume for a single, readable workflow that can be
  imported anywhere.

## 6. Gotchas found while building it
- A governance group's owner PATCHed to an identity that doesn't exist is **cleared** (no error). So check that the new
  owner exists before changing anything (the workflow's *Get New Owner* step).
- Never put a credential inline in an HTTP step created through the API (§3). Use Parameter Storage.
- Create, then update: the first POST loses the parameter links (§3).
- Uploading a workflow in the UI loses them too. Re-pick the parameter in each HTTP step.
- A demo or sandbox tenant may have the `terminated` lifecycle state **disabled**, so the trigger never fires. Test
  with the test endpoint (`ownership.py test`).
- `newLifecycleState` is the **technical** name (`terminated`), not the display name (`Terminated`).
