# API contract alignment: current and future SailPoint API versions

How each API that Bulk Access Request uses lines up with SailPoint's versioning model: `v3`, the dated versions
(`v2024`, `v2025`, `v2026`), `beta`, experimental APIs, and the per-service paths (`/<service>/v1`). Checked on
2026-10-08, updated 2026-10-09 (Launcher submit as the signed-in user, verified end to end with non-admins), and on
2026-10-13: **every call moved to `/<service>/v1`** (Phase C), the deprecated executions list was dropped (Phase B),
and the bulk approver was removed (the plugin now files the access requests itself). The full list of calls is in
[API_INVENTORY.md](API_INVENTORY.md).

Evidence: **[L]** verified live on a test tenant on the `/v1` path, **[L\*]** verified live on the earlier dated path
(the `/v1` path answers GETs and has the same contract, but this call hasn't been re-run on `/v1`), **[S]** from the
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
All paths are defined once: `core/bulkaccess/api.py` (Python) and `plugin/src/app/bulk/api-paths.ts` (plugin). Every
`/v1` GET was probed **without** the experimental header and answered 200 [L]; the Launcher installer's writes (form,
workflow, Launcher, access profile) succeeded on `/v1` [L]. "Was" is the version used until 2026-10-13.

| Service and calls used | Was | Now | Status | Notes |
|---|---|---|---|---|
| OAuth `POST /oauth/token` | not versioned | unchanged | none | — |
| Identities: `GET /identities/v1`, `/identities/v1/{id}` | v2025 | `/identities/v1` **[L]** | ✅ migrated | There was never a v3 identities API |
| Public identities (plugin, non-admins): `GET /public-identities/v1` | v3 | `/public-identities/v1` **[L]** | ✅ migrated | Filters and `sorters=name` as before |
| Form definitions: GET, POST, PATCH, DELETE | v2025 | `/form-definitions/v1[/{id}]` **[L]** (GET, POST, PATCH) | ✅ migrated | DELETE [S] |
| Form instances: list (e2e), GET, PATCH (e2e, and the plugin in launcher mode) | v2025 | `/form-instances/v1[/{id}]` **[L]** (GET), PATCH [L\*] | ✅ migrated | A non-admin can't list instances (403) but can GET and PATCH their own [L\*] |
| Interactive processes, plugin (launcher mode only): `GET /beta/interactive-processes/{id}/blocks` | **beta** | **beta** (unchanged) | 🟠 **Beta only, and undocumented.** No v1, v2025 or v2026 `/blocks` path; interactive processes appear neither on developer.sailpoint.com nor in `sailpoint-oss/api-specs` [D]+[S]. ISC's own Launchpad calls this beta path [L]. Works with a user session as the process owner [L, non-admin]; the admin PAT gets 401 | The one beta call left. The default submit mode (`"direct"`) doesn't use it (see §5 #3) |
| Workflows: list, GET, POST, PUT, PATCH, DELETE (installer) | v2025 | `/workflows/v1[/{id}]` **[L]** | ✅ migrated | Only the Launcher's workflow now; the plugin's own workflow is gone |
| ~~Workflows, plugin: `POST /workflows/{id}/test`~~ | v2025 | — | ✅ **no longer called** | `plugin.submit: "test-endpoint"` was removed (an old config maps to `"direct"`, with a note) |
| ~~`GET /workflows/{id}/executions`~~ (status) | v2025 | — | ✅ **no longer called** (Phase B) | Deprecated in the spec, "removed in July 2028" [S], no replacement documented. `status` no longer lists runs |
| Workflow executions (e2e): `GET /{id}`, `/{id}/history-v2`, `POST /{id}/cancel` | v2025 (`/history`) | `/workflow-executions/v1/{id}[/history-v2\|/cancel]` **[L]** (GET) | ✅ migrated | History moved to **`history-v2`** (`{history:[…]}`), as the migration page maps it; cancel [S] |
| Launchers: list, GET, POST, PUT, DELETE (installer), `…/launch` (e2e, plugin in launcher mode) | v2025 | `/launchers/v1[/{id}[/launch]]` **[L]** (GET, POST, PUT), launch [L\*] | ✅ migrated | Not in beta for `launch` [S], yet the Launchpad itself calls `/beta/launchers/{id}/launch` [L] |
| Entitlements: `?filters=value eq …` (Launcher entitlement), `?filters=requestable eq true[ and name sw …]&sorters=name` (catalog), `/{id}/entitlement-request-config` (no-approval check) | v2025 | `/entitlements/v1` **[L]** | ✅ migrated | `/entitlements/v1` is the v2025 lineage (same contract). `/entitlements/v2` (v2026 lineage) also answers [L]; assess it separately |
| Access profiles: list, POST, PATCH, DELETE; `?filters=id in (…)` (approval schemes) | v3 | `/access-profiles/v1[/{id}]` **[L]** | ✅ migrated | No header [L] |
| Roles: `GET /roles/v1?filters=id in (…)` (approval schemes, installer) | — (new) | `/roles/v1` **[L]** | new | Used to find roles with no approval |
| Access request config: `GET /access-request-config/v1` (installer) | — (new) | `/access-request-config/v1` **[L]** | new | The entitlement default scheme, and whether users may request for others (direct mode needs it) |
| Requestable objects: `GET /requestable-objects/v1` | v3 | `/requestable-objects/v1` **[L]** | ✅ migrated | Asked for `ACCESS_PROFILE`/`ROLE` only; entitlements come from `/entitlements` (§3 #9). Also the plugin's submit gate (`filters=id eq "<Launcher Access>"`, `requestStatus: ASSIGNED`) [L, non-admin] |
| Access requests: `POST /access-requests/v1` (installer `--grant`; **the plugin's direct submit**) | v3 | `/access-requests/v1` **[L]** | ✅ migrated | As a non-admin: ≤ 10 `requestedFor`, all three item types, per-item `comment` and `removeDate`; answer `newRequests[]` (+ `existingRequests[]`) [L] |
| Access request status: `GET /access-request-status/v1` | v3 | `/access-request-status/v1` **[L]** | ✅ migrated | `requested-by=<me>` works for a non-admin and lists their direct requests, with `approvalDetails[].currentOwner` [L] |
| Search: `POST /search/v1` (plugin, admins) | v3 | `/search/v1` **[L]** | ✅ migrated | Same rows as v3 [L] |
| Accounts: `GET /accounts/v1` (plugin, admins) | v3 | `/accounts/v1` **[L]** | ✅ migrated | [D] warns that the new Accounts API rejects filter operators the old one tolerated; the plugin's `name sw`, `name in`, `nativeIdentity in` and `identityId in` were re-tested and return the same rows [L] |
| Generic approvals: list, `GET /{id}`, `/{id}/approve`, `/{id}/reject`, `bulk-approve`, `bulk-reject` (Approvals tab); pending list (`status`) | v2025 | `/generic-approvals/v1[…]` (list and GET **[L]**, POSTs [L\*]) | ✅ migrated | Not experimental (works without the header [L]). `requesterId=` listing and `bulk-cancel` are no longer used (there's no bulk approval) |
| *(Alternative, not used)* access-request approvals: `/access-request-approvals/v1/pending`, `bulk-approve` | — | `/access-request-approvals/v1/pending` **[L]**, `/v1/bulk-approve` [D] (≤ 50; ORG_ADMIN or `idn:access-request-administration:write`) | — | Not needed: generic approvals cover the non-admin case |
| **UI plugins** (`sail`; installer `resolve-alias`) | `/ui-plugins/v1` | `/ui-plugins/v1` (unchanged) | 🔴 **Experimental**: without `X-SailPoint-Experimental` the call returns 400 [L]. *"Experimental, gated capability"* [D] | None available. Pin the `sail` version and re-test every release |
| Workflow library (verification only) | v2025 | `/workflow-library/v1/actions` **[L]** | — | — |

**Workflow step versions** (the definitions aren't path-versioned; checked against `/workflow-library/v1/actions` [L]):

| Step | Version used | Library status [L] |
|---|---|---|
| `sp:access:manage` | v2 | current. v1 still listed, not deprecated |
| `sp:get-identity` | v2 | current. v1 is `deprecated: true` |
| `sp:send-email` | v2 | current. v1 is `deprecated: true` |
| `sp:loop:iterator`, `sp:interactive-form`, `sp:interactive-message` | v1 | not deprecated |
| `sp:generic-approval` | no longer used (no bulk approval) | not deprecated |
| `sp:create-approval-request` | not used | `deprecated: true` |

All library `deprecatedBy` dates read `2099-01-01`, so no removal is scheduled.

## 3. Known live deviations from the spec
These are relied on or worked around in the code. All are **[L]** unless marked otherwise. They were seen on the
dated paths; the `/v1` paths carry the same contracts.

| # | API | Spec or docs say | Live behaviour | How the solution handles it |
|---|---|---|---|---|
| 1 | `POST /generic-approvals/v1/{id}/reject` | `204` [S] | **`200` with the approval body** (as a non-admin) | Accepts either |
| 2 | `GET /generic-approvals/v1?approverId=<other>` | "Must match the calling user's identity ID unless they are an admin" [S] | Non-admin → **400** "approverId must match the calling user's identity ID" | Uses `mine=true` instead |
| 3 | `GET /generic-approvals/v1?include-comments=true` | Adds comments [S] | Rows gain `comments[]` but **lose `assignedTo`** (admin and non-admin) | Never relies on `assignedTo` |
| 4 | `GET /generic-approvals/v1` (list) | — | `approvedBy` / `rejectedBy` are **empty**. Only `GET /{id}` names the decider | One detail GET per ambiguous row |
| 5 | `POST /generic-approvals/v1/bulk-approve` and `bulk-reject` | ORG_ADMIN, `sp:approvals:write` [S]; summary *"on behalf of the caller"* | **`202 {}` even for unknown IDs**. At most **50**. Non-admin → **403 even on their own approvals** | ORG_ADMIN only (`useBulkEndpoint: auto`). Re-reads every ID afterwards |
| 6 | Generic approvals: deciding someone else's approval | — | ORG_ADMIN: allowed, recorded as a **manual reassignment**. Non-admin: **403** | Approvals tab acts only on `mine=true` rows |
| 7 | Generic approvals: comment text | — | Comment text **can't be searched or filtered** | Groups by INC in the browser |
| 8 | Generic approvals list | Defaults to `mine=true` for non-admins and `mine=false` for admins [S] | As documented | Always sends `mine=true` |
| 9 | `GET /requestable-objects/v1?types=` | `types` enum is **`ACCESS_PROFILE`, `ROLE` only** [S] | `types=ENTITLEMENT` alone → **400**. Repeated `types=…&types=ENTITLEMENT` → 200, but **no entitlement rows** | `requestable-objects` is asked for access profiles and roles only; requestable entitlements come from `GET /entitlements/v1?filters=requestable eq true` [L]. "Already has it" for entitlements: identity search `access[]` (held) and `access-request-status?request-state=EXECUTING` (pending) |
| 10 | `POST /access-requests/v1` as a non-admin | — | Works for other people when the tenant allows *request for others*; ≤ 10 people; each request goes through its item's own approval; items with no scheme are granted at once; if the requester is an item's approver, ISC reassigns that approval to an admin | The plugin's default submit (`"direct"`); the installer checks `requestOnBehalfOfConfig` and falls back to the Launcher |
| 11 | `POST /launchers/v1/{id}/launch` | ORG_ADMIN, `sp:launcher-user:launch` [S] | Any identity holding the Launcher's `assignedLaunchers` entitlement can launch it, from the Launchpad **and through the API with their own session** (`{}` → `{interactiveProcessId}`, verified as a non-admin). A non-admin can't list launchers (500 "insufficient authorization") but can GET one | Access through the *Launcher Access* profile. The plugin's launcher mode; the installer writes the Launcher ID into the runtime config |
| 11a | `GET /form-instances/v1` (list) vs `/{id}` | — | A non-admin gets **403** on the list but can GET and PATCH their own instance by ID; through the API the form accepts 250 people, items and SELECT values outside its STATIC options, and unknown keys (only its REGEX rules apply) | The plugin finds the instance through the process's blocks and validates first |
| 12 | Form instances `PATCH` | — | May need **two PATCHes** (ASSIGNED → IN_PROGRESS → SUBMITTED). A failing REGEX leaves it IN_PROGRESS with `formErrors`, and the workflow waits forever | e2e cancels the run |
| 13 | Form definitions | — | **MAX_LENGTH on a TEXTAREA** stops the submission reaching the workflow. A SELECT allows at most **30**. An INTERNAL identity picker only matches complete usernames | No MAX_LENGTH. The Launcher caps people at 30 |
| 14 | `sp:access:manage` | — | At most **10 recipients** per request. Nested loops are rejected | One loop over people, all items per request |
| 15 | `sp:loop:iterator` | — | Hard limit of **250** items ("exceed 250 iteration limit") | In launcher mode the plugin sends parts of ≤ 250 |
| 16 | `sp:serial:iterator` | — | **Silently stops after 50** items | Not used |
| 17 | `sp:create-approval-request` | — | Breaks on one-item lists. Now marked deprecated | Not used |
| 18 | `sp:compare-boolean` | — | The string `"true"` is treated as false | Uses a TOGGLE (a real boolean) |
| 19 | Workflow variables | — | "Update Variable" only accepts variables from a step named "Define Variable…". You can't define a literal `""` | Replace transform empties a placeholder |
| 20 | `GET /access-request-approvals/v1/pending` without `owner-id` | — | Earlier (v3): 400 "owner-id fields must be specified". Later, as an ORG_ADMIN PAT: 200 | Not used. Shown to illustrate drift |
| 21 | Deprecation signalling | `X-Deprecated: true` [D] | `Deprecation: <date>` and `Link rel="deprecation"` (the link returns 404) | Monitor both header names |
| 22 | Rate limit | 429 with `Retry-After` [D] | The plugin SDK doesn't expose response headers | The plugin spaces Approvals-tab calls at ≤ 8/s and backs off on 429 there; direct submit sends ≤ 3 access requests at once |
| 23 | Edge | — | Cloudflare in front of some tenants rejects Python's default User-Agent (error 1010) | Sends `User-Agent: bulk-access-request/1.0` |

## 4. Migration plan
| Phase | When | What | Status |
|---|---|---|---|
| A | Before 2027-03-31 | Plugin off v3 Workflows (`/test`, executions), which sent `Deprecation: 31 Mar 2027`. | ✅ **Done** (2026-10-08, to v2025); since 2026-10-13 the plugin calls no workflow API at all |
| B | Before 2028-07 | `status`: stop relying on `GET /workflows/{id}/executions` (deprecated, removal July 2028). | ✅ **Done** (2026-10-13): the call was dropped; `status` no longer lists runs |
| C | Before **Q2 2028** (end of support) | Move every `/v3/*` and `/v2025/*` path to `/<service>/v1`, from one constants module per language; re-test the Accounts filters; run the e2e again. | ✅ **Done** (2026-10-13): `core/bulkaccess/api.py`, `plugin/src/app/bulk/api-paths.ts`. All GETs [L], Accounts filters [L], Launcher installer writes [L]; workflow history now `history-v2`. Remaining [L\*] calls are re-checked by the next live end-to-end run. One rate-limit bucket now |
| D | Ongoing | `/ui-plugins/v1` is experimental: pin the `sail` version, watch release notes, and re-run `plugin/install.py --deploy` in a sandbox before each upgrade. | Ongoing |
| E | When SailPoint publishes one | A GA path for a Launcher run's blocks (or the form instance ID in the launch response), to retire the last beta call | Waiting on SailPoint |

## 5. Risks and what to watch
| # | Risk | Severity | Watch / mitigation |
|---|---|---|---|
| 1 | ~~The plugin's submit path used **v3 Workflows**, which sends a deprecation header dated 2027-03-31.~~ | ✅ Resolved | The plugin no longer calls workflows. Still watch for `Deprecation` and `Sunset` headers on every call. |
| 2 | **UI Plugins is experimental and gated**: an opt-in header, possible breaking changes without notice, a product licence (`idn:ui-plugins` / `idn:ui-plugins-author`), and SDK `0.0.x`. | 🔴 High | Deployment A (the Launcher) has none of these dependencies, so keep it as the fallback. Get SailPoint to confirm GA plans and the licence for the production tenant. |
| 3 | ~~Plugin submission depended on the workflow test endpoint (ORG_ADMIN only).~~ **Resolved**: the default `"direct"` mode files `POST /access-requests/v1` as the signed-in user, all GA [L, non-admin]. What remains is in the fallback `"launcher"` mode only: (a) the poll for the form uses **`/beta/interactive-processes/{id}/blocks`** (beta, no GA path); (b) the form PATCH goes through `fetch` with the SDK's token, because the SDK has no PATCH. | 🟢 Low | Prefer `"direct"`. Watch for a GA interactive-processes path. |
| 4 | ~~All legacy versions (v3, beta, v202X) lose support in Q2 2028 and stop working in Q1 2029, and everything here used them.~~ | ✅ Resolved (Phase C) | Only the beta `/blocks` call (launcher mode) remains, see #11. |
| 5 | Undocumented **generic-approvals behaviour** (reject 200, bulk 202 for unknown IDs, `assignedTo` dropped with comments, non-admin bulk 403) could be "fixed" in a new major version. | 🟡 Low–Medium | The code already tolerates both forms and confirms every decision. Re-run the Approvals tab tests on each move to a new version. |
| 6 | **Entitlements aren't offered by `requestable-objects`** (outside the spec enum, and none returned live). | ✅ Resolved | Both deployments read requestable entitlements from `GET /entitlements/v1?filters=requestable eq true` [L]. Residual: the `requestable` flag doesn't apply segments or per-identity rules like the Request Center does, and the entitlement "already has it" check depends on the identities search index. |
| 7 | ~~`GET /workflows/{id}/executions` is deprecated (July 2028) with no documented replacement.~~ | ✅ Resolved (Phase B) | No longer called. |
| 8 | Workflow engine limits (loop 250, 10 recipients, form SELECT 30) aren't in any spec. They were found live and could change. | 🟡 Low | `launcher/e2e.py` and the plugin's parts logic. Ask SailPoint to confirm the limits for the production tenant. |
| 9 | **Rate limit** of 100/10 s per client_id and API version. The Approvals tab issues up to ~8/s; direct submit ≤ 3 in flight. | 🟡 Low | Keep `approvals.concurrency` low. Expect 429s if other integrations share the client. |
| 10 | The docs contradict each other about the v3 and beta timeline, and the deprecation link returns 404. | ℹ Info | Ask SailPoint for the authoritative dates. |
| 11 | **Beta dependency in launcher mode: `GET /beta/interactive-processes/{id}/blocks`.** Undocumented, no v1 or dated equivalent, and under the legacy-beta end of life (Q2 2028 support, Q1 2029 EOL). If it changes, the plugin can't find the form after launching and says so after 30 s; nothing is submitted (the launched run is left waiting on its unfilled form). | 🟡 Low (fallback mode only) | Use `"direct"`. The Launchpad form (deployment A) is unaffected. Ask SailPoint for a supported way to find a launched process's form instance. |
| 12 | **Direct mode needs the tenant's *request for others*** setting (`allowRequestOnBehalfOfAnyoneByAnyone`). If an admin turns it off later, plugin submits fail with 403. | 🟡 Low | The installer checks it on every `apply` and falls back to launcher mode; the page explains a 403. |
| 13 | **The no-approval list is a snapshot**: the installer reads each catalog item's approval setup at `apply` time and writes it into the plugin's runtime config. An item whose schemes change afterwards is labelled wrongly until the next `apply`. | 🟡 Low | Re-run `bulkaccess.py apply` after changing approval schemes (or on a schedule). The label is a warning only; SailPoint's own schemes always decide. |
