# API contract alignment: current and future SailPoint API versions

How each API that Bulk Access Request uses lines up with SailPoint's versioning model: `v3`, the dated versions
(`v2024`, `v2025`, `v2026`), `beta`, experimental APIs, and the new per-service paths (`/<service>/v1`). Checked
on 2026-10-08. The full list of calls is in [API_INVENTORY.md](API_INVENTORY.md).

Evidence: **[L]** verified live on a test tenant (read-only GETs, or earlier end-to-end runs), **[S]** from the
OpenAPI specs in `sailpoint-oss/api-specs`, **[D]** from developer.sailpoint.com.

## 1. SailPoint's versioning policy (summary)
| Topic | Policy | Source |
|---|---|---|
| **New model** | Each service is versioned on its own: `/<service>/v1/…` (for example `/identities/v1`). The major version only changes on a breaking change. Additions are made in place. | [D] *API Versioning Strategy* |
| **Breaking change** | A new major version (v2) is released **experimental**, alongside v1. When v2 is ready, v1 is deprecated, which **starts two years** of end of life. | [D] |
| **Experimental** | Needs `X-SailPoint-Experimental: true`. Without it the call is refused. *"May introduce breaking changes with little or no notice and are not suitable for production use cases."* | [D], and [L] for `/ui-plugins/v1` |
| **How a deprecation is announced** | Announcements (Admin UI, Compass, newsletters), "Deprecated" in the spec, and a response header (the docs say `X-Deprecated: true`). | [D] |
| **Deprecation header as actually sent** | `Deprecation: Wed, 31 Mar 2027 00:00:00 UTC` and `Link: <…/idn/api/deprecation-policy>; rel="deprecation"`. The linked page returns **404**. | [L] |
| **Legacy APIs** | *"Beta, V3 and the yearly versioned APIs (v202X) will be deprecated in favor of the new versioning process… supported until **Q2 of 2028**… end-of-life and no longer function **Q1 of 2029**."* | [D] *API Versioning Strategy* |
| ⚠ **Inconsistency** | The *API Versioning Migration* page also says *"The V3 and Beta APIs are unaffected by this change and will remain operational under the previously communicated timeline."* Plan for the stricter date (Q1 2029). | [D] |
| **`/latest`** | *"Inherently unsafe for production integrations."* (Not used here.) | [D] |
| **Exceptions** | Security, performance and delivery reasons may justify breaking changes without notice. | [D] |
| **Rate limit** | 100 requests per **client_id and API version** per 10 s, then 429 with `Retry-After`. | [D] *Rate Limiting* |
| **Path mapping** | The migration page maps every legacy path to its new version. Almost everything goes to `v1`. Exceptions: `/entitlements` (v2026 lineage) → `v2`, and workflow execution history → `history-v2`. | [D] *API Versioning Migration* |
| **Naming** | developer.sailpoint.com now calls the platform **"SailPoint Human Fabric (SHF)"**. | [D] |

## 2. Endpoint by endpoint
"Per-service path" is the new-model equivalent. **[L]** means a GET on that exact path returned 200 on the test
tenant. Paths marked [D] are documented but weren't called, either because the method isn't GET or to avoid side effects.

| Service and calls used | Version used | Newest dated version | Per-service path | Deprecation status | Recommended migration |
|---|---|---|---|---|---|
| OAuth `POST /oauth/token` | not versioned | — | unchanged | none | none |
| Identities: `GET /identities`, `/identities/{id}` | v2025 | v2026 | `/identities/v1`, `/identities/v1/{id}` **[L]** | Legacy EOL Q1 2029 [D] | → `/identities/v1` |
| Form definitions: GET, POST, PATCH, DELETE | v2025 | v2026 | `/form-definitions/v1[/{id}]` **[L]** (GET) | Legacy EOL | → `/form-definitions/v1` |
| Form instances: list, GET, PATCH (e2e only) | v2025 | v2026 | `/form-instances/v1[/{id}]` **[L]** (GET) | Legacy EOL | → `/form-instances/v1` |
| Workflows: list, GET, POST, PUT, PATCH, DELETE (installer) | v2025 | v2026 | `/workflows/v1[/{id}]` **[L]** (GET) | No header on v2025 [L]. Legacy EOL | → `/workflows/v1` |
| Workflows, plugin: `GET /v2025/workflows`, `POST /v2025/workflows/{id}/test` | v2025 (moved from v3) | v2026 | `/workflows/v1`, `POST /workflows/v1/{id}/test` [D] | ✅ **Done.** v3 (and beta) Workflows send `Deprecation: 31 Mar 2027` [L]; `GET /v2025/workflows` returns the same rows with no header [L]. The v2025 `/test` operation has the same request, response and ORG_ADMIN level as v3 [S] (not called live: it starts a run) | → `/workflows/v1/{id}/test` in Phase C |
| Workflow executions, plugin: `GET /v2025/workflow-executions/{id}` | v2025 (moved from v3) | v2026 | `/workflow-executions/v1/{id}` **[L]** | ✅ **Done.** v3 sent `Deprecation: 31 Mar 2027` [L]; v2025 returns the same keys (`id, workflowId, requestId, status, startTime, closeTime`) for the same execution, with no header [L] | → `/workflow-executions/v1/{id}` in Phase C |
| `GET /workflows/{id}/executions` (status only) | v2025 | v2026 | `/workflows/v1/{id}/executions` **[L]** | 🟠 **Deprecated in the spec, "removed in July 2028"** [S]. The v1 page is marked deprecated too [D]. No header [L]. | No replacement is documented. Keep it as diagnostics only, or drop it from `status` |
| Workflow executions: `GET /{id}`, `/{id}/history`, `POST /{id}/cancel` (e2e) | v2025 | v2026 | `/workflow-executions/v1/{id}[/history\|/history-v2\|/cancel]` **[L]** (GET) | Legacy EOL. The SDK migration notes say history maps to **V2** | → `/workflow-executions/v1/{id}/history-v2` |
| Launchers: list, GET, POST, PUT, DELETE, `…/launch` | v2025 | v2026 | `/launchers/v1[/{id}[/launch]]` **[L]** (GET) | Legacy EOL. Not in beta for `launch` [S] | → `/launchers/v1` |
| Entitlements: `GET /entitlements?filters=value eq …` (Launcher entitlement), `?filters=requestable eq true[ and name sw …]&sorters=name` (catalog, both deployments) | v2025 | v2026 | `/entitlements/v1` (v2025 lineage) **[L]** and `/entitlements/v2` (v2026 lineage) **[L]** | Legacy EOL | → `/entitlements/v1` (same contract). Assess v2 separately |
| Access profiles: list, POST, PATCH, DELETE | **v3** | v2026 | `/access-profiles/v1[/{id}]` **[L]** (GET) | Legacy EOL. No header [L] | → `/access-profiles/v1` |
| Requestable objects: `GET /requestable-objects` | **v3** | v2026 | `/requestable-objects/v1` **[L]** | Legacy EOL. No header [L] | → `/requestable-objects/v1`. Asked for `ACCESS_PROFILE`/`ROLE` only; entitlements come from `/entitlements` (§3 #9) |
| Access requests: `POST /access-requests` (`--grant`) | **v3** | v2026 | `/access-requests/v1` [D] | Legacy EOL | → `/access-requests/v1` |
| Access request status: `GET /access-request-status` | **v3** | v2026 | `/access-request-status/v1` **[L]** | Legacy EOL. No header [L] | → `/access-request-status/v1` |
| Search: `POST /search` (plugin) | **v3** | v2026 | `/search/v1` [D] | Legacy EOL. Not probed (POST) | → `/search/v1` |
| Accounts: `GET /accounts` (plugin) | **v3** | v2026 | `/accounts/v1` **[L]** | Legacy EOL. ⚠ [D] warns that the new Accounts API returns 400 for filter operators that were tolerated before (example: `name co`) | → `/accounts/v1`, and **re-test** the plugin's `name sw`, `name in`, `nativeIdentity in` and `identityId in` filters |
| Generic approvals: list, `GET /{id}`, `/{id}/approve`, `/{id}/reject`, `bulk-approve`, `bulk-reject`, `bulk-cancel` | v2025 | v2026 (same paths, plus `/{id}/cancel`) | `/generic-approvals/v1[/{id}[/approve\|/reject]]`, `/generic-approvals/v1/bulk-…` (list and GET **[L]**, POSTs [D]) | Legacy EOL. Not experimental (works without the header [L]) | → `/generic-approvals/v1` |
| *(Alternative, not used)* access-request approvals: `/access-request-approvals/pending`, `bulk-approve` | — | v2026 (`bulk-approve` only in v2026 [S]) | `/access-request-approvals/v1/pending` **[L]**, `/v1/bulk-approve` [D] (≤ 50; ORG_ADMIN or `idn:access-request-administration:write`) | — | Not needed: generic approvals cover the non-admin case |
| **UI plugins (`sail`)** | `/ui-plugins/v1` | none (`/beta` and `/v2025` return 404 [L]) | `/ui-plugins/v1` | 🔴 **Experimental**: without `X-SailPoint-Experimental` the call returns 400 [L]. *"Experimental, gated capability"* [D] | None available. Pin the `sail` version and re-test every release |
| Workflow library (verification only) | v2025 | v2026 | `/workflow-library/v1/actions` **[L]** | — | — |

**Workflow step versions** (the definitions aren't path-versioned):

| Step | Version used | Library status [L] |
|---|---|---|
| `sp:access:manage` | v2 | current. v1 still listed, not deprecated |
| `sp:get-identity` | v2 | current. v1 is `deprecated: true` |
| `sp:send-email` | v2 | current. v1 is `deprecated: true` |
| `sp:generic-approval`, `sp:loop:iterator`, `sp:interactive-form`, `sp:interactive-message` | v1 | not deprecated |
| `sp:create-approval-request` | not used | `deprecated: true` |

All library `deprecatedBy` dates read `2099-01-01`, so no removal is scheduled.

## 3. Known live deviations from the spec
These are relied on or worked around in the code. All are **[L]** unless marked otherwise.

| # | API | Spec or docs say | Live behaviour | How the solution handles it |
|---|---|---|---|---|
| 1 | `POST /v2025/generic-approvals/{id}/reject` | `204` [S] | **`200` with the approval body** (as a non-admin) | Accepts either |
| 2 | `GET /v2025/generic-approvals?approverId=<other>` | "Must match the calling user's identity ID unless they are an admin" [S] | Non-admin → **400** "approverId must match the calling user's identity ID" | Uses `mine=true` instead |
| 3 | `GET /v2025/generic-approvals?include-comments=true` | Adds comments [S] | Rows gain `comments[]` but **lose `assignedTo`** (admin and non-admin) | Never relies on `assignedTo` |
| 4 | `GET /v2025/generic-approvals` (list) | — | `approvedBy` / `rejectedBy` are **empty**. Only `GET /{id}` names the decider. (`include-approvers=true` [S] returns `approvers` and `approvedBy` keys [L], but isn't used yet.) | One detail GET per ambiguous row |
| 5 | `POST /v2025/generic-approvals/bulk-approve` and `bulk-reject` | ORG_ADMIN, `sp:approvals:write` [S]; summary *"on behalf of the caller"* | **`202 {}` even for unknown IDs**. At most **50**. Non-admin → **403 even on their own approvals** | ORG_ADMIN only (`useBulkEndpoint: auto`). Re-reads every ID afterwards |
| 6 | Generic approvals: deciding someone else's approval | — | ORG_ADMIN: allowed, recorded as a **manual reassignment**. Non-admin: **403** | Approvals tab acts only on `mine=true` rows |
| 7 | Generic approvals: comment text | — | Comment text **can't be searched or filtered** | Groups by INC in the browser |
| 8 | Generic approvals list | Defaults to `mine=true` for non-admins and `mine=false` for admins [S] | As documented | Always sends `mine=true` |
| 9 | `GET /v3/requestable-objects?types=` | `types` enum is **`ACCESS_PROFILE`, `ROLE` only**, comma-separated (`explode: false`) [S] | A comma list containing `ENTITLEMENT` → 400. `types=ENTITLEMENT` alone → **400**. Repeated `types=…&types=ENTITLEMENT` → 200, but **no entitlement rows** (the tenant has 10 requestable entitlements) | ✅ Fixed: `requestable-objects` is asked for access profiles and roles only, and requestable entitlements come from `GET /v2025/entitlements?filters=requestable eq true` [L] (it returned the 10). "Already has it" for entitlements: identity search `access[]` (held) and `access-request-status?request-state=EXECUTING` (pending) [L] |
| 10 | `POST /v2025/workflows/{id}/test` (was v3) | ORG_ADMIN [S]. *"Workflow must be disabled"* [D] | As documented. It *"will cause a live run"* [D] | Plugin workflow is kept disabled. Only ORG_ADMIN can submit |
| 11 | `POST /v2025/launchers/{id}/launch` | ORG_ADMIN, `sp:launcher-user:launch` [S] | Any identity holding the Launcher's `assignedLaunchers` entitlement can launch it **from the Launchpad** | Access through the *Launcher Access* profile |
| 12 | Form instances `PATCH` | — | May need **two PATCHes** (ASSIGNED → IN_PROGRESS → SUBMITTED). A failing REGEX leaves it IN_PROGRESS with `formErrors`, and the workflow waits forever | e2e cancels the run |
| 13 | Form definitions | — | **MAX_LENGTH on a TEXTAREA** stops the submission reaching the workflow. A SELECT allows at most **30**. An INTERNAL identity picker only matches complete usernames | No MAX_LENGTH. The Launcher caps people at 30 |
| 14 | `sp:access:manage` | — | At most **10 recipients** per request. Nested loops are rejected | One loop over people, all items per request |
| 15 | `sp:loop:iterator` | — | Hard limit of **250** items ("exceed 250 iteration limit") | The plugin sends parts of ≤ 250 |
| 16 | `sp:serial:iterator` | — | **Silently stops after 50** items | Not used |
| 17 | `sp:create-approval-request` | — | Breaks on one-item lists. Now marked deprecated | Not used |
| 18 | `sp:compare-boolean` | — | The string `"true"` is treated as false | Uses a TOGGLE (a real boolean) |
| 19 | Workflow variables | — | "Update Variable" only accepts variables from a step named "Define Variable…". You can't define a literal `""` | Replace transform empties a placeholder |
| 20 | `GET /v3/access-request-approvals/pending` without `owner-id` | — | Earlier: 400 "owner-id fields must be specified". **Today, as an ORG_ADMIN PAT: 200** | Not used. Shown to illustrate drift |
| 21 | Deprecation signalling | `X-Deprecated: true` [D] | `Deprecation: <date>` and `Link rel="deprecation"` (the link returns 404) | Monitor both header names |
| 22 | Rate limit | 429 with `Retry-After` [D] | The plugin SDK doesn't expose response headers | The plugin spaces calls at ≤ 8/s and backs off on 429 |
| 23 | Identities | — | There is **no v3 identities API** | Uses `/v2025/identities` |
| 24 | Edge | — | Cloudflare in front of some tenants rejects Python's default User-Agent (error 1010) | Sends `User-Agent: bulk-access-request/1.0` |

## 4. Migration plan
| Phase | When | What | Effort |
|---|---|---|---|
| A | ✅ **Done** (before 2027-03-31) | Plugin `bulk-api.service.ts` now uses `/v2025/workflows`, `/v2025/workflows/{id}/test` and `/v2025/workflow-executions/{id}`, like the installers. The Python scripts had no `/v3/workflows` or `/beta` calls left. | — |
| B | Before 2028-07 | `status`: stop relying on `GET /workflows/{id}/executions` (deprecated, removal July 2028). | Small |
| C | Before **Q2 2028** (end of support) | Move every remaining `/v3/*` and `/v2025/*` path to `/<service>/v1`. All of them have v1 equivalents, and 23 new-model paths answered 200 on GET. Gather the path constants (`launcher/install.py`, `plugin/pluginlib.py`, `core/bulkaccess/tenant.py`, `plugin/src/app/bulk/bulk-api.service.ts`) and run the full e2e again. Re-test the Accounts filters. Note: the rate limit applies per API version, so mixed versions mean separate buckets. | Medium |
| D | Ongoing | `/ui-plugins/v1` is experimental: pin the `sail` version, watch release notes, and re-run `plugin/install.py --deploy` in a sandbox before each upgrade. | — |

## 5. Risks and what to watch
| # | Risk | Severity | Watch / mitigation |
|---|---|---|---|
| 1 | ~~The plugin's submit path uses **v3 Workflows** (`/test`, executions), which already sends a **Deprecation header dated 2027-03-31**.~~ Resolved: moved to v2025, which sends no deprecation header [L]. | ✅ Resolved | Phase A done. Still watch for `Deprecation` and `Sunset` headers on every call. |
| 2 | **UI Plugins is experimental and gated**: an opt-in header, possible breaking changes without notice, a product licence (`idn:ui-plugins` / `idn:ui-plugins-author`), and SDK `0.0.x`. | 🔴 High | Deployment A (the Launcher) has none of these dependencies, so keep it as the fallback. Get SailPoint to confirm GA plans and the licence for the production tenant. |
| 3 | Plugin submission depends on the **workflow test endpoint** (ORG_ADMIN, disabled workflow, *"a live run"*). It's a test feature, not an integration contract. | 🟠 Medium | Long term, a backend holding the external-trigger OAuth client (see `plugin/README.md`). Ask SailPoint whether `/test` is supported for production use. |
| 4 | **All legacy versions (v3, beta, v202X) lose support in Q2 2028 and stop working in Q1 2029**, and everything here uses them. | 🟠 Medium | Phase C. Track the *API Versioning Strategy* page. |
| 5 | Undocumented **generic-approvals behaviour** (reject 200, bulk 202 for unknown IDs, `assignedTo` dropped with comments, non-admin bulk 403) could be "fixed" in a new major version. | 🟡 Low–Medium | The code already tolerates both forms and confirms every decision. Re-run the Approvals tab tests on each move to a new version. |
| 6 | **Entitlements aren't offered by `requestable-objects`** (outside the spec enum, and none returned live). | ✅ Resolved | Both deployments read requestable entitlements from `GET /v2025/entitlements?filters=requestable eq true` [L]. Residual: the `requestable` flag doesn't apply segments or per-identity rules like the Request Center does, and the entitlement "already has it" check depends on the identities search index (unindexed identities get no warning). |
| 7 | `GET /workflows/{id}/executions` is deprecated (July 2028) with no documented replacement. | 🟡 Low | Used only by `status`. |
| 8 | Workflow engine limits (loop 250, 10 recipients, form SELECT 30) aren't in any spec. They were found live and could change. | 🟡 Low | `launcher/e2e.py` and the plugin's parts logic. Ask SailPoint to confirm the limits for the production tenant. |
| 9 | **Rate limit** of 100/10 s per client_id and API version. The Approvals tab issues up to ~8/s. | 🟡 Low | Keep `approvals.concurrency` low. Expect 429s if other integrations share the client. |
| 10 | The docs contradict each other about the v3 and beta timeline, and the deprecation link returns 404. | ℹ Info | Ask SailPoint for the authoritative dates for the paths in §2. |
