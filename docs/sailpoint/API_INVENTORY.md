# API inventory: everything Bulk Access Request uses in ISC

**For SailPoint and platform teams.** This lists every SailPoint API call, workflow step, form feature and CLI
command the solution depends on. Use it to check that a customer's production tenant offers all of them.
It was compiled on 2026-10-08 by searching the code (`core/bulkaccess/*.py`, `launcher/*.py`, `plugin/*.py`,
`bulkaccess.py` and `plugin/src/app/**/*.ts`, leaving out specs and demo code). It was then checked against the
`sailpoint-oss/api-specs` OpenAPI files, developer.sailpoint.com, and a live test tenant.

Updated on 2026-10-13:
- **Every call moved to SailPoint's GA per-service paths** (`/<service>/v1`). Each path is defined once, in
  `core/bulkaccess/api.py` (Python) and `plugin/src/app/bulk/api-paths.ts` (plugin).
- **The bulk approver was removed.** Nothing asks one person to approve a bulk request any more; each item is approved
  the way SailPoint has it set up (owner, manager, …), and items with no approval of their own are granted right away.
- **The plugin files the access requests itself** (`plugin.submit: "direct"`, the default), as the signed-in user.
  The workflow test endpoint (`"test-endpoint"`) is gone.

Updated on 2026-10-14:
- **Who may submit is a role.** The plugin installer creates the requestable role "<prefix> Bulk Access Request - Submit
  Access" (§1 #32–35) and requests it for holders of the *Launcher Access* profile (§1 #36, #29). With the Launcher
  deployed the role contains that profile, which is then not requestable on its own. The plugin's gate reads the role.

Related: [API_CONTRACT_ALIGNMENT.md](API_CONTRACT_ALIGNMENT.md) (versions, deprecations, how to migrate) ·
[REQUIREMENTS_CHECKLIST.md](REQUIREMENTS_CHECKLIST.md) (a checklist to run against a tenant) ·
[END_TO_END_WALKTHROUGH.md](END_TO_END_WALKTHROUGH.md) (the lifecycle step by step, with the calls each step makes).

**Evidence tags:**

| Tag | Meaning |
|---|---|
| **[L]** | Verified live on the `/v1` path against a test tenant ([CLAUDE.md](../../CLAUDE.md), [CONTRACTS.md](../dev/CONTRACTS.md)). |
| **[L\*]** | Verified live on the earlier dated path (`/v3` or `/v2025`); the `/v1` path has the same contract [D] and answered GETs [L], but this write or user-session call hasn't been re-run on `/v1` yet. |
| **[S]** | From the OpenAPI spec in `sailpoint-oss/api-specs` (`x-sailpoint-userLevels`, `security`, `deprecated`). |
| **[D]** | From developer.sailpoint.com. |

"UL" means user levels and "scopes" means OAuth scopes, as the spec states them for the same operation (the `/v1`
paths carry the operations of the dated versions they replace). "—" means the spec doesn't say; "not checked" means
the spec wasn't consulted for that call (added on 2026-10-13).

## 0. At a glance
| Component | Credentials | Who | API calls (distinct) |
|---|---|---|---|
| **Installer CLI** (`bulkaccess.py`, `launcher/*.py`, `plugin/*.py`, `core/bulkaccess/catalog.py`) | Personal access token (PAT), `client_credentials`, scope `sp:scopes:all` | An ORG_ADMIN (or the `owner` service identity) | 36 |
| **E2E test** (`launcher/e2e.py`, optional) | The same PAT | ORG_ADMIN who also holds Launcher access | 8 (plus the installer's lookups) |
| **UI plugin** (Angular page inside ISC) | The signed-in user's ISC session, through `@sailpoint/ui-plugin-sdk` (manifest `apiScopes: ["sp:scopes:all"]`) | *New request*: any holder of the *Submit Access* role. *My bulk requests*: the user's own. *Approvals* tab: any item approver | 19 |
| **`sail` CLI** (`plugin/install.py --deploy`, `plugin/uninstall.py --plugin`) | The same PAT, passed through environment variables | ORG_ADMIN with UI-plugin rights | 7 (all `/ui-plugins/v1`, experimental) |
| **Workflow engine** (the Launcher's workflow) | Runs as the workflow owner | — | 6 actions, 3 operators, 1 trigger, 2 transforms |

**API versions in use:** `/<service>/v1` everywhere, plus two exceptions:
- **`/beta/interactive-processes/{id}/blocks`**: only the plugin, only in `plugin.submit: "launcher"`. There's no GA
  path for it; ISC's own Launchpad uses the same `/beta` path.
- **`/ui-plugins/v1`**: experimental (`X-SailPoint-Experimental: true`), through `sail`, and the installer's one
  `resolve-alias` lookup. There's no other API for UI plugins.

And `POST /oauth/token` (not versioned). **Not used:** `/v3`, `/v2024`, `/v2025`, `/v2026`, `/latest`.

## 1. Installer CLI (PAT of an ORG_ADMIN)
`core/bulkaccess/tenant.py` sends every call with `Authorization: Bearer`, a custom `User-Agent`, JSON, and
`application/json-patch+json` for PATCH.

| # | Method and path | Used for | Called from | UL [S] | Scopes [S] | Status |
|---|---|---|---|---|---|---|
| 1 | `POST /oauth/token` (`grant_type=client_credentials`) | Exchanges the PAT for a JWT | `tenant.py` | — | — | GA, not versioned [L] |
| 2 | `GET /identities/v1/{id}` | The PAT's identity (`identity_id` claim), which owns everything created | `tenant.me()` | — | `idn:identity:read` | GA [L] |
| 3 | `GET /form-definitions/v1?filters=name eq …` | Find the Launcher form by name | `launcher/install.py`, `e2e.py`, `bulkaccess.py export` | ORG_ADMIN | `sp:forms:read` | GA [L] |
| 4 | `POST /form-definitions/v1` | Create the intake form | `launcher/install.py` | ORG_ADMIN | `sp:forms:manage` | GA [L] |
| 5 | `PATCH /form-definitions/v1/{id}` | Replace `formElements`, `formConditions`, `description` (catalog sync) | `launcher/install.py` | — | `sp:forms:manage` | GA [L] |
| 6 | `DELETE /form-definitions/v1/{id}` | Uninstall | `launcher/uninstall.py` | — | `sp:forms:manage` | GA [S] |
| 7 | `GET /workflows/v1?limit=250` | Find workflows by name (the Launcher's; and the old plugin workflow, which uninstall removes) | `launcher/install.py`, `pluginlib.py`, `e2e.py`, `export` | — | `sp:workflow:read` | GA [L] |
| 8 | `POST /workflows/v1` | Create the Launcher workflow (then update it with its own ID) | `launcher/install.py` | — | `sp:workflow:manage` | GA [L] |
| 9 | `GET /workflows/v1/{id}` | Read back; `status` checks `enabled`, the trigger filter and the Manage Access version | `launcher/install.py`, `launcher/status.py` | — | `sp:workflow:read` | GA [L] |
| 10 | `PUT /workflows/v1/{id}` | Update the definition (always sent disabled) | `launcher/install.py` | — | `sp:workflow:manage` | GA [L] |
| 11 | `PATCH /workflows/v1/{id}` (`/enabled`) | Disable before PUT or DELETE; re-enable afterwards | `launcher/install.py`, `launcher/uninstall.py` | — | `sp:workflow:manage` | GA [L] |
| 12 | `DELETE /workflows/v1/{id}` | Uninstall (the Launcher workflow; the plugin's old "(Plugin)" workflow if it's still there) | `launcher/uninstall.py`, `plugin/uninstall.py` | — | `sp:workflow:manage` | GA [S] |
| 13 | `GET /launchers/v1?limit=100` | Find the Launcher by name (`{items:[…]}`) | `launcher/install.py`, `pluginlib.py`, `e2e.py`, `export` | ORG_ADMIN | `sp:launcher-admin:read` | GA [L] |
| 14 | `POST /launchers/v1` | Create the Launcher (`type: INTERACTIVE_PROCESS`, `reference: WORKFLOW`) | `launcher/install.py` | ORG_ADMIN | `sp:launcher-admin:write` | GA [L] |
| 15 | `GET /launchers/v1/{id}` | Poll `disabled` after a workflow update | `launcher/install.py` | ORG_ADMIN | `sp:launcher-admin:read` | GA [L] |
| 16 | `PUT /launchers/v1/{id}` | Update the Launcher, and re-enable it (it switches itself off when its workflow is disabled) | `launcher/install.py` | ORG_ADMIN | `sp:launcher-admin:write` | GA [L] |
| 17 | `DELETE /launchers/v1/{id}` | Uninstall | `launcher/uninstall.py` | ORG_ADMIN | `sp:launcher-admin:delete` | GA [S] |
| 18 | `GET /entitlements/v1?filters=value eq "<launcherId>"` | Find the auto-created `assignedLaunchers` entitlement (polled: it appears asynchronously) | `launcher/install.py` | Any | `idn:entitlement:read` | GA [L] |
| 19 | `GET /access-profiles/v1?filters=name eq …` | Find the *Launcher Access* profile (the plugin installer puts it in the *Submit Access* role) | `launcher/install.py`, `launcher/status.py`, `launcher/uninstall.py`, `plugin/install.py`, `export` | ORG_ADMIN, ROLE_ADMIN, ROLE_SUBADMIN, SOURCE_ADMIN, SOURCE_SUBADMIN | `idn:access-profile:read` | GA [L] |
| 20 | `POST /access-profiles/v1` | Create it (MANAGER approval or none; requestable only without the plugin) | `launcher/install.py` | same as 19 | `idn:access-profile:manage`, `idn:entitlement:read`, `idn:identity:read`, `idn:sources:read` | GA [L] |
| 21 | `PATCH /access-profiles/v1/{id}` | Update only what differs: description, entitlement, `requestable`, `accessRequestConfig` | `launcher/install.py` | ORG_ADMIN, SOURCE_ADMIN, SOURCE_SUBADMIN | `idn:access-profile:manage` | GA [L] |
| 22 | `DELETE /access-profiles/v1/{id}` | Uninstall | `launcher/uninstall.py` | same as 21 | `idn:access-profile:manage` | GA [S] |
| 23 | `GET /requestable-objects/v1?types=…&types=…&limit=250&offset=N` | The Request Center catalog's access profiles and roles (only the configured ones among `ACCESS_PROFILE`, `ROLE`) | `catalog.requestable_objects` (both installers) | ORG_ADMIN (no `identity-id` means admin only) | `idn:requestable-objects:read` | GA [L]. See the `ENTITLEMENT` note in §6 |
| 24 | `GET /entitlements/v1?filters=requestable eq true[ and name sw "…"]&sorters=name&limit=250&offset=N` | Requestable entitlements (when `catalog.types` has `ENTITLEMENT`) | `catalog.requestable_objects` | Any | `idn:entitlement:read` | GA [L] |
| 25 | `GET /access-profiles/v1?filters=id in (…)` (50 IDs per call) | Each catalog access profile's `accessRequestConfig.approvalSchemes` | `catalog.no_approval_ids` (both installers) | same as 19 | `idn:access-profile:read` | GA [L] |
| 26 | `GET /roles/v1?filters=id in (…)` (50 IDs per call) | Each catalog role's `accessRequestConfig.approvalSchemes` | `catalog.no_approval_ids` | not checked | not checked | GA [L] |
| 27 | `GET /entitlements/v1/{id}/entitlement-request-config` | Each catalog entitlement's own approval schemes (one call per entitlement, at install time only) | `catalog.no_approval_ids` | not checked | not checked | GA [L] |
| 28 | `GET /access-request-config/v1` | (a) `entitlementRequestConfig.grantRequestApprovalSchemes`: the default for entitlements with no schemes of their own. (b) `requestOnBehalfOfConfig.allowRequestOnBehalfOfAnyoneByAnyone`: whether `plugin.submit: "direct"` can work (otherwise the installer falls back to `"launcher"`) | `catalog.no_approval_ids`, `pluginlib.may_request_for_others` | not checked | not checked | GA [L] |
| 29 | `POST /access-requests/v1` | `--grant`: request the *Submit Access* role (or, without the plugin, the *Launcher Access* profile) for given identities (`GRANT_ACCESS`); and request the role for holders of the profile who lack it, 10 per request | `submit_access.request` (`plugin/install.py`, `launcher/install.py`) | ORG_ADMIN, USER | `idn:access-request:manage` | GA [L] |
| 30 | `GET /generic-approvals/v1?include-comments=true&limit=250&filters=status eq "PENDING" and type eq "ACCESS_REQUEST_APPROVAL"` | `status`: pending item approvals whose comment is a bulk comment, counted per INC | `plugin/status.py` | APPROVAL_OWNER | `idn:access-request-approvals:read` | GA [L] |
| 31 | `GET /ui-plugins/v1/resolve-alias?alias=…` (with `X-SailPoint-Experimental: true`) | The plugin's ID, so the Launcher's emails can link to the plugin page | `tenant.plugin_instance_id` (`launcher/install.py`) | — | — | **Experimental** [L] |
| 32 | `GET /roles/v1?filters=name eq …` | Find the *Submit Access* role | `submit_access.find_named` (`plugin/install.py`, `plugin/status.py`, `plugin/uninstall.py`, `launcher/uninstall.py`, `export`) | not checked | not checked | GA [L] |
| 33 | `POST /roles/v1` | Create the *Submit Access* role: requestable, MANAGER approval or none, `accessProfiles` = the *Launcher Access* profile (with the Launcher) or `[]`, `entitlements: []` (an empty role is accepted) | `submit_access.apply_role` (`plugin/install.py`) | not checked | not checked | GA [L] |
| 34 | `PATCH /roles/v1/{id}` | Update only what differs (description, `accessProfiles`, `requestable`, `accessRequestConfig`); `launcher/uninstall.py` takes the profile out of `accessProfiles` | `submit_access.apply_role`, `launcher/uninstall.py` | not checked | not checked | GA [D] |
| 35 | `DELETE /roles/v1/{id}` | Uninstall (succeeds while the role is assigned) | `plugin/uninstall.py` | not checked | not checked | GA [L] |
| 36 | `POST /search/v1` (`indices: ["identities"]`, query `@access(id:"<id>")`) | Holders of the *Launcher Access* profile and of the role: who gets the role (migration); `plugin/status.py` counts holders | `submit_access.holders` (`plugin/install.py`, `plugin/status.py`) | not checked | `sp:search:read` | GA [L] |

Dropped since 2026-10-09: `GET /workflows/{id}/executions` (deprecated, removal July 2028; `status` no longer lists
runs), the plugin workflow's `POST`/`PUT` (there's no plugin workflow), and the bulk approvals listed in `status`.

## 2. E2E test (`launcher/e2e.py`, PAT of an ORG_ADMIN who also holds Launcher access)
It finds the form, workflow and Launcher with the installer's lookups (§1 #3, #7, #13), then:

| Method and path | Used for | UL [S] | Scopes [S] | Status |
|---|---|---|---|---|
| `POST /launchers/v1/{id}/launch` | Start the Launcher as the PAT user | ORG_ADMIN | `sp:launcher-user:launch` | GA [L\*]. Live, any holder of `assignedLaunchers` can launch |
| `GET /form-instances/v1?limit=50` | Find the form instance the launch created | ORG_ADMIN | `sp:forms:read` | GA [L] |
| `GET /form-instances/v1/{id}`, `PATCH /form-instances/v1/{id}` | Fill in and submit the form (it may take two PATCHes, ASSIGNED → IN_PROGRESS → SUBMITTED) | USER | `[]` | GA [L\*] |
| `GET /workflow-executions/v1/{id}` | Run status | ORG_ADMIN | `sp:workflow-execution:read` | GA [L] |
| `GET /workflow-executions/v1/{id}/history-v2` | Which steps ran (`{history:[…]}`) | ORG_ADMIN, SOURCE_ADMIN, SOURCE_SUBADMIN | `sp:workflow-execution:read` | GA [L] (the migration maps history to `history-v2`) |
| `POST /workflow-executions/v1/{id}/cancel` | Cancel a run stuck on a form that failed validation | — | `sp:workflow-execute:external` | GA [S] |
| `GET /access-request-status/v1?requested-for=<id>&limit=20` | Live mode: each person got one request per item, with the bulk comment (no "Approved by") and, for temporary access, a `removeDate` | ORG_ADMIN (others) | `idn:access-request-status:read` | GA [L] |

The e2e test no longer approves anything: there's no bulk approval. Each request goes on to its item's own approval.

## 3. UI plugin (the signed-in user's session)
All calls go through `SailpointPluginService.get/post` → `@sailpoint/ui-plugin-sdk` `api.get/post` (SDK `^0.0.3`,
`@sailpoint/angular-sdk ^0.0.2`); the one PATCH goes through `fetch` with the SDK's token. The page calls no other
host: the manifest's `contentSecurityPolicies` is empty.

| Tab or feature | Method and path | Used for | UL [S] | Scopes [S] | Status |
|---|---|---|---|---|---|
| People search (ORG_ADMIN only; 403/400 for others) | `POST /search/v1` (`indices: ["identities"]`) | Type-ahead, and fallback resolution of pasted names and emails | ORG_ADMIN, CERT_ADMIN, REPORT_ADMIN, SOURCE_ADMIN, SOURCE_SUBADMIN, ROLE_ADMIN, ROLE_SUBADMIN | `sp:search:read` | GA [L] (same rows as v3) |
| People search (ORG_ADMIN only) | `GET /accounts/v1?filters=name sw … \| name in … \| nativeIdentity in … \| identityId in …` | Find identities not indexed yet | ORG_ADMIN, SOURCE_ADMIN, SOURCE_SUBADMIN, HELPDESK | `idn:accounts:read` | GA [L] (all four filters re-tested on v1) |
| Paste a list (ORG_ADMIN only) | `GET /identities/v1?filters=id in (…)` and `alias eq … or email eq …` (50 per call) | Resolve pasted IDs, usernames and emails | — | `idn:identity:read` | GA [L] |
| Paste a list (ORG_ADMIN only) | `GET /identities/v1/{id}` | Last-resort lookup of a single ID | — | `idn:identity:read` | GA [L] |
| People search, paste a list (non-admins) | `GET /public-identities/v1?limit=…&sorters=name&filters=…` (`displayName`/`alias`/`email`/`firstname`/`lastname` `sw` for type-ahead; `id in (…)`, `alias eq … or email eq …` for pasted lists) | Find people without admin rights | **Any user** | — | GA [L]. Department only in `attributes[]`; no fallbacks for identities it doesn't list |
| Access step | `GET /requestable-objects/v1?identity-id=<me>&types=…&limit=250&offset=N[&filters=name sw …]` | The catalog's access profiles and roles | ORG_ADMIN; **any user with their own `identity-id`** (403 without it) | `idn:requestable-objects:read` | GA [L] |
| Access step | `GET /entitlements/v1?filters=requestable eq true[ and name sw …]&sorters=name&limit=250&offset=N` | The catalog's requestable entitlements (rows carry `source.name`, no `type`) | Any | `idn:entitlement:read` | GA [L] |
| Access step (ORG_ADMIN only) | `POST /search/v1` (`indices: ["accessprofiles","entitlements"]`) | Source names to show next to access profiles | see above | `sp:search:read` | GA [L] |
| Access step, review step | *(no call)* | "No approval: granted right away" next to items with no approval of their own: the list (`noApproval`) is written into the runtime config by the installer (§1 #25–28) | — | — | — |
| Review step | `GET /requestable-objects/v1?identity-id=<id>&types=…&filters=id in (…)` | "Already has it" for access profiles and roles (up to 100 people) | ORG_ADMIN; a non-admin may pass another person's ID | `idn:requestable-objects:read` | GA [L] |
| Review step (ORG_ADMIN only) | `POST /search/v1` (`indices: ["identities"]`, `id:(…)`, includes `access.id`, `access.type`) | "Already has it" for entitlements (held), 100 people per call | see People search | `sp:search:read` | GA [L]. Identities missing from the index get no warning |
| Review step (ORG_ADMIN only) | `GET /access-request-status/v1?requested-for=<id>&request-state=EXECUTING&limit=250` | "Already requested" for entitlements (the row `id` is the item's ID) | ORG_ADMIN; any user for their own [D] | `idn:access-request-status:read` | GA [L] |
| **Submit gate** (both modes) | `GET /requestable-objects/v1?identity-id=<me>&types=ROLE&limit=1&filters=id eq "<submitAccessId>"` | Does the user hold the *Submit Access* role (`requestStatus: ASSIGNED`)? If not, nothing is sent and the page says which role to request. Skipped when the runtime config has no role ID (a dry-run install) | any user, for their own `identity-id` | `idn:requestable-objects:read` | GA [L, non-admin] (2026-10-14) |
| **Submit** (`"direct"`, the default) | `POST /access-requests/v1` `{requestedFor: [≤ 10 IDs], requestType: GRANT_ACCESS, requestedItems: [{type, id, comment, removeDate?}]}` | File every chosen item for up to 10 people **as the signed-in user**, 3 calls at a time; the response's `newRequests[]` and `existingRequests[]` are counted | ORG_ADMIN, USER | `idn:access-request:manage` | GA [L, non-admin]. Needs the tenant's "request for others" setting (§1 #28). Each request then goes through its item's own approval |
| Submit (`"launcher"`) | `POST /launchers/v1/{id}/launch` `{}` | Start the Launcher once per part (≤ 250 people), as the signed-in user → `{interactiveProcessId}` | ORG_ADMIN [S]; live: **any holder of the Launcher's `assignedLaunchers` entitlement** | `sp:launcher-user:launch` | GA [L\*] (verified as a non-admin on v2025). Without access: 401/403, or 500 "insufficient authorization" |
| Submit (`"launcher"`) | `GET /beta/interactive-processes/{ipid}/blocks` | Poll (1 s, up to 30 s) until the `FORM` block names the form instance; then (every 2–5 s, up to 2 min) look for the workflow's `INFO` message ("Requested: …") or `ERROR` message (stopped) | the process owner [L] | — | **Beta** [L] (as a non-admin; the admin PAT gets 401: needs a user session). No GA path |
| Submit (`"launcher"`) | `PATCH /form-instances/v1/{id}` (`application/json-patch+json`: `/formData`, `/state` `SUBMITTED`), `GET /form-instances/v1/{id}` | Fill in and submit the Launcher form; repeat up to 3 times until SUBMITTED/COMPLETED or `formErrors` | USER | `[]` | GA [L\*] (as a non-admin, who can't *list* form instances: 403) |
| My bulk requests | `GET /access-request-status/v1?requested-by=<me>&limit=250&offset=N&sorters=-created` | The user's own access requests, grouped by the INC in their comment; per request: state, `approvalDetails[].currentOwner` (who it waits for), `removeDate` | ORG_ADMIN; any user for their own [D] | `idn:access-request-status:read` | GA [L, non-admin]. Requests filed through the Launcher belong to the workflow owner, so they don't show here |
| **Approvals** | `GET /generic-approvals/v1?mine=true&include-comments=true&limit=250&offset=N&sorters=createdDate&filters=status eq "PENDING" and type eq "ACCESS_REQUEST_APPROVAL"` | The caller's pending item approvals, with the item comment holding the INC | APPROVAL_OWNER | `idn:access-request-approvals:read` | GA [L] |
| **Approvals** | `POST /generic-approvals/v1/{id}/approve` and `…/reject` `{comment}` | Decide as the caller (default path, ≤ 8 per second) | APPROVAL_OWNER | `idn:access-request-approvals:manage` | GA [L\*] (as a non-admin on v2025) |
| **Approvals** | `POST /generic-approvals/v1/bulk-approve` and `…/bulk-reject` `{approvalIds ≤ 50, comment}` | Fast path, ORG_ADMIN only | ORG_ADMIN | `sp:approvals:write` | GA [L\*]. A non-admin gets 403 |
| **Approvals** | `GET /generic-approvals/v1?limit=250&filters=approvalId in (…)` | Confirm every decision (50 IDs per call) | APPROVAL_OWNER | `idn:access-request-approvals:read` | GA [L] |
| **Approvals** | `GET /generic-approvals/v1/{id}` | Who decided, only for IDs whose status changed although our call failed | APPROVAL_OWNER | same | GA [L] |

**What a non-admin can do in the plugin** (verified 2026-10-08 with two `sp:user` sessions, and the submit, gate and
My bulk requests calls again on `/v1` on 2026-10-13; the page branches on `context.user.capabilities.isOrgAdmin`):

| Feature | Non-admin | How |
|---|---|---|
| Submit | ✔ with the *Submit Access* role | Gate: `requestable-objects?identity-id=<me>&types=ROLE` (ASSIGNED). Then `"direct"`: `POST /access-requests/v1`; or `"launcher"`: launch, interactive-process blocks, own form instance |
| People search, pasted lists | ✔ | `/public-identities/v1` (identities, search and accounts are 403) |
| Catalog | ✔ | `requestable-objects?identity-id=<me>` (403 without it), `/entitlements/v1` |
| "Already has it" | access profiles and roles ✔; entitlements skipped (said on the review step) | `requestable-objects?identity-id=<person>`; search is 403 and another person's `access-request-status` is 400 |
| My bulk requests | ✔ for requests filed directly | `access-request-status?requested-by=<me>` |
| Approvals tab | ✔ | `generic-approvals` (APPROVAL_OWNER) |

**Direct submit, call by call** (`plugin.submit: "direct"`; all as the signed-in user's session):

| # | Call | Answer used | Notes |
|---|---|---|---|
| 1 | `GET /public-identities/v1?filters=…` (non-admins) or the admin lookups above | identity IDs for the people | People step |
| 2 | `GET /requestable-objects/v1?identity-id=<me>&types=ACCESS_PROFILE&types=ROLE…`, `GET /entitlements/v1?filters=requestable eq true…` | catalog items `{id, type, name}` | `identity-id` is required for non-admins (403 without it) |
| 3 | `GET /requestable-objects/v1?identity-id=<me>&types=ROLE&limit=1&filters=id eq "<submitAccessId>"` | `requestStatus` | Not `ASSIGNED` → "To submit, you need '<role>'. Request it in the Request Center." and nothing is sent |
| 4 | `POST /access-requests/v1`, once per 10 people, 3 in flight | `newRequests[]`, `existingRequests[]` | Every item carries the bulk comment `INC… \| Bulk access request by <me> \| <access> \| <justification>` and, for temporary access, `removeDate`. A 401/403 stops the rest; any other failure can be retried for just those people |
| 5 | `GET /access-request-status/v1?requested-by=<me>&sorters=-created` | the requests, grouped by INC | My bulk requests |

**Launcher submit, call by call** (`plugin.submit: "launcher"`; once per part of ≤ 250 people):

| # | Call | Answer used | Notes |
|---|---|---|---|
| 1–3 | as in direct mode | | |
| 4 | `POST /launchers/v1/{launcherId}/launch` `{}` | `interactiveProcessId` | Launcher ID from the runtime config |
| 5 | `GET /beta/interactive-processes/{interactiveProcessId}/blocks` (every 1 s, up to 30 s) | the `FORM` block's form instance ID | **Beta**; the same call ISC's Launchpad makes. Needs a user session |
| 6 | `PATCH /form-instances/v1/{id}` (`/formData`, then `/state` `SUBMITTED`), `GET /form-instances/v1/{id}` | `state`, `formErrors` | Up to 3 PATCHes; sent with `fetch` and the SDK's token. The form has no approver field |
| 7 | `GET /beta/interactive-processes/{id}/blocks` again (up to 2 min) | the workflow's `INFO` message ("Requested: …") or `ERROR` message ("Invalid INC number: …") | No answer within 2 min: shown as submitted, "you'll get an email with the result" |

The Launcher's workflow files those requests in its owner's name (the PAT user), so they don't appear on the
requester's *My bulk requests*; the requester gets the "Requested" email instead.

## 4. `sail` CLI (≥ 2.7.0)
Run by `plugin/pluginlib.py` with `SAIL_BASE_URL`, `SAIL_CLIENT_ID` and `SAIL_CLIENT_SECRET` in its environment
(never on the command line). The endpoints come from the CLI's source (`cmd/ui_plugins/*.go`, 2.7.0) and binary
strings. **Every one sends `X-SailPoint-Experimental: true`.**

| Command (when used) | Endpoints | Right needed [D] |
|---|---|---|
| `sail ui-plugins list --json` (installer: find by alias; `status --plugin`) | `GET /ui-plugins/v1?limit=…&offset=…` | `idn:plugins-ui:read` |
| `sail ui-plugins create [--private]` (first install) | `GET /ui-plugins/v1/validate-alias?alias=…`, `POST /ui-plugins/v1`. Writes the tenant's dev CSP into `angular.json` | `idn:plugins-ui:create` |
| `sail ui-plugins push-manifest [--private]` (every later install; **replaces the whole manifest**) | `GET /ui-plugins/v1/resolve-alias?alias=…`, `PATCH /ui-plugins/v1/{pluginInstanceId}` | `idn:plugins-ui:update` |
| `sail ui-plugins upload` (every `--deploy`) | Uploads the built assets as an asset bundle (`/ui-plugins/v1/{id}/asset-bundles…`; inferred from the binary strings, not traced) | `idn:plugins-ui:update` |
| `sail ui-plugins delete <id> --force` (`plugin/uninstall.py --plugin`) | `DELETE /ui-plugins/v1/{id}` | `idn:plugins-ui:delete` |
| *(OAuth)* | `POST /oauth/token` | — |

- **Live [L]:** `GET /ui-plugins/v1` without the header returns 400 "Experimental Header 'X-SailPoint-Experimental' is
  missing or invalid", and 200 with it. `/beta/ui-plugins` and `/v2025/ui-plugins` return 404.
- **Licence [CLI strings]:** the CLI's error text says the tenant needs the **`idn:ui-plugins` or
  `idn:ui-plugins-author`** product licence.
- **[D]:** developer.sailpoint.com/docs/ui-plugins/prerequisites says *"UI Plugins are an experimental, gated
  capability"*.

## 5. Workflow engine: steps the Launcher's workflow uses
Built by `core/bulkaccess/definitions.py` (`bulk_workflow`). There is one workflow: the plugin no longer has its own.
**Library status** comes from `GET /workflow-library/v1/actions` (and `operators`, `triggers`) [L].

| Step type (`actionId`, version) | Step names | What it does | Library status [L] |
|---|---|---|---|
| Trigger `idn:interactive-process-launched` (EVENT) | — | Fires on any Launcher launch. Filtered with `filter.$: $[?(@.workflowId == '<own id>')]` | present, not deprecated |
| `sp:interactive-form` v1 | *Interactive Form* | Shows the form in the Launchpad (`formDefinitionId`, `interactiveProcessId`) | v1, not deprecated |
| `sp:get-identity` **v2** | *Get Requester* | The requester's name and email | v2 current (**v1 deprecated**, not used) |
| `sp:compare-strings` (StringMatches) | *INC Valid?*, *Part Given?*, *Duration Valid?*, *Unit Valid?*, *Within Limit?* | Checks before anything is requested | present |
| `sp:compare-boolean` (BooleanEquals) | *Temporary?* (form TOGGLE) | Branch on the access type | present |
| `sp:define-variable` | *Define Variable Access* | `removeDuration`, `accessLabel` and `partLabel`, with transform `sp:transform:replace:string` | present |
| `sp:update-variable` | *Set Temporary Access*, *Set Part* | Builds `"30d"` and the label, with transform `sp:transform:concatenate:string`; copies the plugin's part label | present |
| `sp:interactive-message` v1 | *Notify Requested* (INFO), *Reject Bad INC* / *Reject Bad Duration* / *Reject Bad Unit* / *Reject Too Long* (ERROR) | The answer in the Launchpad (and, in launcher submit mode, what the plugin reads from the blocks) | v1, not deprecated |
| `sp:loop:iterator` v1 | *Request Access* (live mode only) | One iteration per person (≤ 250), `context.$: "$"` | v1, not deprecated |
| `sp:access:manage` **v2** (inside the loop) | *Manage Access* | `GRANT_ACCESS`, all items, `removeDuration` (`""` = permanent), comment `INC… \| Bulk access request by … \| … \| …` | v2 current |
| `sp:send-email` **v2** | *Email Requested*, *Email Reject …* | "Requested: …" (dry run: "Checked: …") or "Not requested: …" to the requester, or to `overrideRecipients` | v2 current (**v1 deprecated**, not used) |
| `sp:operator-success` and `sp:operator-failure` | *End Step - Success*, *End Step - Rejected* | End steps (failure carries `failureName` and `description`) | present |

**Deliberately not used:** `sp:generic-approval` (there's no bulk approval any more), `sp:create-approval-request`
(the library marks it **deprecated** [L], and it breaks on one-item lists), and `sp:serial:iterator` (it silently
stops after 50 items) [L].

## 6. Objects created, and the features they rely on
| Object | API | Features relied on |
|---|---|---|
| **Form definition** "<prefix> Bulk Access Request Form" | `/form-definitions/v1` | SECTION. SELECT with an **INTERNAL IDENTITY** data source (`maximum` ≤ 30) and with **STATIC** options holding full `{id,type,name}` objects (`maximum` ≤ 25). TEXT with **REGEX** validation. TEXTAREA (no MAX_LENGTH). **TOGGLE**. No HIDDEN element (it makes the Launchpad render an empty form); the plugin's `partLabel` is an extra formData key. `formConditions` HIDE effects [L]. No approver field |
| **Workflow** "<prefix> Bulk Access Request" | `/workflows/v1` | Enabled, Interactive trigger scoped to its own ID [L] |
| **Launcher** "<prefix> Bulk Access Request" | `/launchers/v1` | `type: INTERACTIVE_PROCESS`, `reference: {type: WORKFLOW}`. ISC auto-creates an `assignedLaunchers` entitlement on the IdentityNow source [L] |
| **Access profile** "<prefix> Bulk Access Request - Launcher Access" | `/access-profiles/v1` | Wraps the `assignedLaunchers` entitlement. `approvalSchemes: [{approverType: MANAGER}]` or none [L]. Requestable only when the plugin isn't deployed; with the plugin, people get it through the role below |
| **Role** "<prefix> Bulk Access Request - Submit Access" (plugin) | `/roles/v1` | Requestable, `approvalSchemes: [{approverType: MANAGER}]` or none; contains the *Launcher Access* profile when the Launcher is deployed, else empty (no source or entitlement needed) [L]. The plugin's submit gate: `requestable-objects` `types=ROLE` → `requestStatus: ASSIGNED` for a holder, from a non-admin's own session [L] |
| **UI plugin** (alias `<prefix>-bulk-access`) | `/ui-plugins/v1` through `sail` | Slot `full-page`, `apiScopes: ["sp:scopes:all"]`, `restrictToUsers` (private = only the installer). Runtime config carries `submit`, `launcherId` (launcher mode), `submitAccessId` / `submitAccessName` (the role) and `noApproval` [L] |
| **Access requests** (runtime) | `POST /access-requests/v1` from the plugin (requester = **the signed-in user**), or Manage Access v2 in the Launcher's workflow (requester = **the workflow owner**) | Each goes through its **item's own approval scheme** [L]. Items with no scheme (an access profile or role with `approvalSchemes: []`; an entitlement with none of its own when the tenant default is none) are **granted at once** [L]. If the requester is also an item's approver, ISC reassigns that approval to an admin [L] |

Removed: the "<prefix> Bulk Access Request (Plugin)" workflow (only `plugin/uninstall.py` still looks for it, to delete
it) and the runtime generic approvals named `Bulk access <INC>[ (k/n)]`.

**`ENTITLEMENT` in the catalog** [L]+[S]: in the spec the `requestable-objects` `types` enum is only `ACCESS_PROFILE`
and `ROLE`. On the test tenant, `types=ENTITLEMENT` alone returns **400**, and the repeated form
(`types=ACCESS_PROFILE&types=ENTITLEMENT`) returns 200 **without any entitlement rows**, although 10 requestable
entitlements exist. So both deployments ask `requestable-objects` for access profiles and roles only, and read
requestable entitlements from `GET /entitlements/v1?filters=requestable eq true` (plus `and name sw "<nameStartsWith>"`),
which returned exactly those 10 [L]. Entitlement rows have no `type`, so the code tags them `ENTITLEMENT`. See the
risks in [API_CONTRACT_ALIGNMENT.md](API_CONTRACT_ALIGNMENT.md#5-risks-and-what-to-watch).

## 7. Scopes: what `sp:scopes:all` covers
Both the PAT and the plugin manifest ask for `sp:scopes:all`. The effective rights are still those of the user
(the PAT owner, or whoever is signed in to the plugin). For a least-privilege review, these are the spec scopes the
calls above need:

| Area | Scopes [S] |
|---|---|
| Identities and search | `idn:identity:read`, `sp:search:read`, `idn:accounts:read` |
| Forms | `sp:forms:read`, `sp:forms:manage` |
| Workflows | `sp:workflow:read`, `sp:workflow:manage`, `sp:workflow-execution:read`, `sp:workflow-execute:external` (e2e cancel) |
| Launchers | `sp:launcher-admin:read`, `sp:launcher-admin:write`, `sp:launcher-admin:delete`, `sp:launcher-user:launch` |
| Access model | `idn:entitlement:read`, `idn:access-profile:read`, `idn:access-profile:manage`, `idn:sources:read`, and whatever reading, creating, updating and deleting roles and the entitlement request config need (not checked), `idn:requestable-objects:read` |
| Access requests | `idn:access-request:manage`, `idn:access-request-status:read`, and reading the access request config (not checked) |
| Approvals | `idn:access-request-approvals:read`, `idn:access-request-approvals:manage`, `sp:approvals:write` (bulk, ORG_ADMIN) |
| UI plugins (`sail`) | rights `idn:plugins-ui:create`, `:read`, `:update`, `:delete` [D] |
