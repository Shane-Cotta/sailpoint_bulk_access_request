# API inventory: everything Bulk Access Request uses in ISC

**For SailPoint and platform teams.** This lists every SailPoint API call, workflow step, form feature and CLI
command the solution depends on. Use it to check that a customer's production tenant offers all of them.
It was compiled on 2026-10-08 by searching the code (`core/bulkaccess/*.py`, `launcher/*.py`, `plugin/*.py`,
`bulkaccess.py` and `plugin/src/app/**/*.ts`, leaving out specs and demo code). It was then checked against the
`sailpoint-oss/api-specs` OpenAPI files, developer.sailpoint.com, and a live test tenant (read-only GETs only).

Related: [API_CONTRACT_ALIGNMENT.md](API_CONTRACT_ALIGNMENT.md) (versions, deprecations, how to migrate) ·
[REQUIREMENTS_CHECKLIST.md](REQUIREMENTS_CHECKLIST.md) (a checklist to run against a tenant).

**Evidence tags:**

| Tag | Meaning |
|---|---|
| **[L]** | Verified live against a test tenant, by these GETs or by the project's earlier end-to-end runs ([CLAUDE.md](../../CLAUDE.md), [CONTRACTS.md](../dev/CONTRACTS.md)). |
| **[S]** | From the OpenAPI spec in `sailpoint-oss/api-specs` (`x-sailpoint-userLevels`, `security`, `deprecated`). |
| **[D]** | From developer.sailpoint.com. |

"UL" means user levels and "scopes" means OAuth scopes, both exactly as the spec states them. "—" means the spec
doesn't say.

## 0. At a glance
| Component | Credentials | Who | API calls (distinct) |
|---|---|---|---|
| **Installer CLI** (`bulkaccess.py`, `launcher/*.py`, `plugin/*.py`) | Personal access token (PAT), `client_credentials`, scope `sp:scopes:all` | An ORG_ADMIN (or the `owner` service identity) | 27 |
| **E2E test** (`launcher/e2e.py`, optional) | The same PAT | ORG_ADMIN | 12 |
| **UI plugin** (Angular page inside ISC) | The signed-in user's ISC session, through `@sailpoint/ui-plugin-sdk` (manifest `apiScopes: ["sp:scopes:all"]`) | *New request*: any holder of the *Launcher Access* profile with `plugin.submit: "launcher"` (default), ORG_ADMIN with `"test-endpoint"`. *My bulk requests*: the user's own. *Approvals* tab: any approver | 19 |
| **`sail` CLI** (`plugin/install.py --deploy`, `plugin/uninstall.py --plugin`) | The same PAT, passed through environment variables | ORG_ADMIN with UI-plugin rights | 7 (all `/ui-plugins/v1`, experimental) |
| **Workflow engine** (the two generated workflows) | Runs as the workflow owner | — | 7 actions, 6 operators, 2 triggers, 2 transforms |

**API versions in use:** `/v2025/*` (most of it), `/v3/*` (access profiles, catalog, access requests, search,
accounts), **`/beta/interactive-processes/{id}/blocks`** (only the plugin, in launcher submit mode: it has no v1 or dated
equivalent yet; ISC's own Launchpad uses the same `/beta` path), `/ui-plugins/v1` (only through `sail`), `/oauth/token`.
**Not used:** `/v2024`, `/v2026`, `/latest`, or `X-SailPoint-Experimental` in the solution's own code.

## 1. Installer CLI (PAT of an ORG_ADMIN)
`core/bulkaccess/tenant.py` sends every call with `Authorization: Bearer`, a custom `User-Agent`, JSON, and
`application/json-patch+json` for PATCH.

| # | Method and path | Used for | Called from | UL [S] | Scopes [S] | Status |
|---|---|---|---|---|---|---|
| 1 | `POST /oauth/token` (`grant_type=client_credentials`) | Exchanges the PAT for a JWT | `tenant.py` | — | — | GA, not versioned [L] |
| 2 | `GET /v2025/identities/{id}` | The PAT's identity (`identity_id` claim), which owns everything created | `tenant.me()`, `pluginlib.me()` | — | `idn:identity:read` | GA [L] |
| 3 | `GET /v2025/form-definitions?filters=name eq …` | Find the Launcher form by name | `launcher/install.py`, `bulkaccess.py export` | ORG_ADMIN | `sp:forms:read` | GA [L] |
| 4 | `POST /v2025/form-definitions` | Create the intake form | `launcher/install.py` | ORG_ADMIN | `sp:forms:manage` | GA [L] |
| 5 | `PATCH /v2025/form-definitions/{id}` | Replace `formElements`, `formConditions`, `description` (catalog sync) | `launcher/install.py` | — | `sp:forms:manage` | GA [L] |
| 6 | `DELETE /v2025/form-definitions/{id}` | Uninstall | `launcher/uninstall.py` | — | `sp:forms:manage` | GA [S] |
| 7 | `GET /v2025/workflows?limit=250` | Find workflows by name | `launcher/install.py`, `pluginlib.py`, `export` | — | `sp:workflow:read` | GA [L] |
| 8 | `POST /v2025/workflows` | Create the Launcher workflow (then update it with its own ID) and the plugin workflow | both installers | — | `sp:workflow:manage` | GA [L] |
| 9 | `GET /v2025/workflows/{id}` | Read back, check `enabled`, trigger filter, Manage Access version | `launcher/install.py`, `launcher/status.py` | — | `sp:workflow:read` | GA [L] |
| 10 | `PUT /v2025/workflows/{id}` | Update the definition (always sent disabled) | both installers | — | `sp:workflow:manage` | GA [L] |
| 11 | `PATCH /v2025/workflows/{id}` (`/enabled`) | Disable before PUT or DELETE; re-enable the Launcher workflow | installers, `launcher/uninstall.py` | — | `sp:workflow:manage` | GA [L] |
| 12 | `DELETE /v2025/workflows/{id}` | Uninstall | both uninstallers | — | `sp:workflow:manage` | GA [S] |
| 13 | `GET /v2025/workflows/{id}/executions?limit=N` | Recent runs, in `status` | `launcher/status.py`, `plugin/status.py` | ORG_ADMIN, SOURCE_ADMIN, SOURCE_SUBADMIN | `sp:workflow-execution:read` | **Deprecated in the spec: "will be removed in July 2028"** [S]; still 200, with no deprecation header [L] |
| 14 | `GET /v2025/launchers?limit=100` | Find the Launcher by name (`{items:[…]}`) | `launcher/install.py`, `export` | ORG_ADMIN | `sp:launcher-admin:read` | GA [L] |
| 15 | `POST /v2025/launchers` | Create the Launcher (`type: INTERACTIVE_PROCESS`, `reference: WORKFLOW`) | `launcher/install.py` | ORG_ADMIN | `sp:launcher-admin:write` | GA [L] |
| 16 | `GET /v2025/launchers/{id}` | Poll `disabled` after a workflow update | `launcher/install.py` | ORG_ADMIN | `sp:launcher-admin:read` | GA [L] |
| 17 | `PUT /v2025/launchers/{id}` | Update the Launcher, and re-enable it (it switches itself off when its workflow is disabled) | `launcher/install.py` | ORG_ADMIN | `sp:launcher-admin:write` | GA [L] |
| 18 | `DELETE /v2025/launchers/{id}` | Uninstall | `launcher/uninstall.py` | ORG_ADMIN | `sp:launcher-admin:delete` | GA [S] |
| 19 | `GET /v2025/entitlements?filters=value eq "<launcherId>"` | Find the auto-created `assignedLaunchers` entitlement (polled, because it appears asynchronously) | `launcher/install.py` | Any | `idn:entitlement:read` | GA [L] |
| 20 | `GET /v3/access-profiles?filters=name eq …` | Find the *Launcher Access* profile | `launcher/install.py`, `uninstall`, `export` | ORG_ADMIN, ROLE_ADMIN, ROLE_SUBADMIN, SOURCE_ADMIN, SOURCE_SUBADMIN | `idn:access-profile:read` | GA (legacy v3) [L] |
| 21 | `POST /v3/access-profiles` | Create it (requestable, MANAGER approval or none) | `launcher/install.py` | same as 20 | `idn:access-profile:manage`, `idn:entitlement:read`, `idn:identity:read`, `idn:sources:read` | GA (legacy v3) [L] |
| 22 | `PATCH /v3/access-profiles/{id}` | Update its entitlement and `accessRequestConfig` | `launcher/install.py` | ORG_ADMIN, SOURCE_ADMIN, SOURCE_SUBADMIN | `idn:access-profile:manage` | GA (legacy v3) [L] |
| 23 | `DELETE /v3/access-profiles/{id}` | Uninstall | `launcher/uninstall.py` | same as 22 | `idn:access-profile:manage` | GA (legacy v3) [S] |
| 24 | `GET /v3/requestable-objects?types=…&types=…&limit=250&offset=N` | The Request Center catalog's access profiles and roles (only the configured ones among `ACCESS_PROFILE`, `ROLE`), used for the form's item choices | `launcher/install.py` | ORG_ADMIN (no `identity-id` means admin only) | `idn:requestable-objects:read` | GA (legacy v3) [L]. See the `ENTITLEMENT` note in §6 |
| 24a | `GET /v2025/entitlements?filters=requestable eq true[ and name sw "…"]&sorters=name&limit=250&offset=N` | Requestable entitlements for the form's item choices (when `catalog.types` has `ENTITLEMENT`) | `launcher/install.py` | Any | `idn:entitlement:read` | GA [L] (filter, paging and sorting verified) |
| 25 | `POST /v3/access-requests` | `--grant`: request the *Launcher Access* profile for given identities (`GRANT_ACCESS`) | `launcher/install.py` | ORG_ADMIN, USER | `idn:access-request:manage` | GA (legacy v3) [L] |
| 26 | `GET /v2025/generic-approvals?limit=250` | Pending `Bulk access …` approvals, in `status` | `plugin/status.py` | APPROVAL_OWNER | `idn:access-request-approvals:read` | GA [L] |
| 27 | `GET /v2025/generic-approvals/{id}` | Approvers of one approval (the list leaves them out) | `plugin/status.py` | APPROVAL_OWNER | same | GA [L] |

## 2. E2E test (`launcher/e2e.py`, PAT of an ORG_ADMIN who also holds Launcher access)
| Method and path | Used for | UL [S] | Scopes [S] | Status |
|---|---|---|---|---|
| `POST /v2025/launchers/{id}/launch` | Start the Launcher as the PAT user | ORG_ADMIN | `sp:launcher-user:launch` | GA [L]. Live, any holder of `assignedLaunchers` can launch from the Launchpad |
| `GET /v2025/form-instances?limit=50` | Find the form instance the launch created | ORG_ADMIN | `sp:forms:read` | GA [L] |
| `GET /v2025/form-instances/{id}`, `PATCH /v2025/form-instances/{id}` | Fill in and submit the form (it may take two PATCHes, ASSIGNED → IN_PROGRESS → SUBMITTED) | USER | `[]` | GA [L] |
| `GET /v2025/workflow-executions/{id}` | Run status | ORG_ADMIN | `sp:workflow-execution:read` | GA [L] |
| `GET /v2025/workflow-executions/{id}/history` | Which steps ran | ORG_ADMIN, SOURCE_ADMIN, SOURCE_SUBADMIN | `sp:workflow-execution:read` | GA [L]. `history-v2` also exists [S] |
| `POST /v2025/workflow-executions/{id}/cancel` | Cancel a run stuck on a form that failed validation | — | `sp:workflow-execute:external` | GA [S] |
| `GET /v2025/generic-approvals?limit=100` | Find the `Bulk access <INC>` approval | APPROVAL_OWNER | `idn:access-request-approvals:read` | GA [L] |
| `POST /v2025/generic-approvals/{id}/approve` and `…/reject` | Decide on the approver's behalf (recorded as a manual reassignment) | APPROVAL_OWNER | `idn:access-request-approvals:manage` | GA [L] |
| `POST /v2025/generic-approvals/bulk-cancel` | Cancel an approval sent to the wrong person | ORG_ADMIN | `sp:approvals:write` | GA [S] |
| `GET /v3/access-request-status?requested-for=<id>&limit=20` | Check each person's request carries the INC and `removeDate` | ORG_ADMIN (others) | `idn:access-request-status:read` | GA (legacy v3) [L] |

## 3. UI plugin (the signed-in user's session)
All calls go through `SailpointPluginService.get/post` → `@sailpoint/ui-plugin-sdk` `api.get/post` (SDK `^0.0.3`,
`@sailpoint/angular-sdk ^0.0.2`). The page calls no other host: the manifest's `contentSecurityPolicies` is empty.

| Tab or feature | Method and path | Used for | UL [S] | Scopes [S] | Status |
|---|---|---|---|---|---|
| People search (ORG_ADMIN only; 403/400 for others ✔) | `POST /v3/search` (`indices: ["identities"]`) | Type-ahead, and fallback resolution of pasted names and emails | ORG_ADMIN, CERT_ADMIN, REPORT_ADMIN, SOURCE_ADMIN, SOURCE_SUBADMIN, ROLE_ADMIN, ROLE_SUBADMIN | `sp:search:read` | GA (legacy v3) [L] |
| People search (ORG_ADMIN only; 403/400 for others ✔) | `GET /v3/accounts?filters=name sw … \| name in … \| nativeIdentity in … \| identityId in …` | Find identities not indexed yet | ORG_ADMIN, SOURCE_ADMIN, SOURCE_SUBADMIN, HELPDESK | `idn:accounts:read` | GA (legacy v3) [L] |
| Paste a list (ORG_ADMIN only; 403/400 for others ✔) | `GET /v2025/identities?filters=id in (…)` and `alias eq … or email eq …` (50 per call) | Resolve pasted IDs, usernames and emails | — | `idn:identity:read` | GA [L] |
| Paste a list (ORG_ADMIN only; 403/400 for others ✔) | `GET /v2025/identities/{id}` | Last-resort lookup of a single ID | — | `idn:identity:read` | GA [L] |
| People search, paste a list (non-admins) | `GET /v3/public-identities?limit=…&sorters=name&filters=…` (`displayName`/`alias`/`email`/`firstname`/`lastname` `sw` for type-ahead; `id in (…)`, `alias eq … or email eq …` for pasted lists) | Find people without admin rights | **Any user** ✔ | — | GA (legacy v3) [L, non-admin]. Same as `/v2025/public-identities`. Department only in `attributes[]`; no fallbacks for identities it doesn't list |
| Access step | `GET /v3/requestable-objects?identity-id=<me>&types=…&limit=250&offset=N[&filters=name sw …]` | The catalog's access profiles and roles | ORG_ADMIN; **any user with their own `identity-id`** ✔ (403 without it) | `idn:requestable-objects:read` | GA (legacy v3) [L] |
| Access step | `GET /v2025/entitlements?filters=requestable eq true[ and name sw …]&sorters=name&limit=250&offset=N` | The catalog's requestable entitlements (rows carry `source.name`, no `type`) | Any | `idn:entitlement:read` | GA [L] |
| Access step (ORG_ADMIN only; 403/400 for others ✔) | `POST /v3/search` (`indices: ["accessprofiles","entitlements"]`) | Source names to show next to access profiles | see above | `sp:search:read` | GA (legacy v3) [L] |
| Review step | `GET /v3/requestable-objects?identity-id=<id>&types=…&filters=id in (…)` | "Already has it" warning for access profiles and roles (up to 100 people) | ORG_ADMIN; ✔ a non-admin may pass another person's ID | `idn:requestable-objects:read` | GA (legacy v3) [L] |
| Review step (ORG_ADMIN only; 403/400 for others ✔) | `POST /v3/search` (`indices: ["identities"]`, `id:(…)`, includes `access.id`, `access.type`) | "Already has it" for entitlements (held), 100 people per call | see People search | `sp:search:read` | GA (legacy v3) [L]. Identities missing from the index get no warning |
| Review step (ORG_ADMIN only; 403/400 for others ✔) | `GET /v3/access-request-status?requested-for=<id>&request-state=EXECUTING&limit=250` | "Already requested" for entitlements (the row `id` is the item's ID) | ORG_ADMIN; any user for their own [D] | `idn:access-request-status:read` | GA (legacy v3) [L] |
| Submit (launcher mode, default) | `POST /v2025/launchers/{id}/launch` `{}` | **Start the Launcher once per part, as the signed-in user** → `{interactiveProcessId}` | ORG_ADMIN [S]; live: **any holder of the Launcher's `assignedLaunchers` entitlement** | `sp:launcher-user:launch` | GA [L] (as a non-admin; the Launchpad itself calls `/beta/launchers/{id}/launch`). Without access: 401/403, or 500 "insufficient authorization" |
| Submit (launcher mode) | `GET /beta/interactive-processes/{ipid}/blocks` | Poll (1 s, up to 30 s) until the `FORM` block names the form instance; later, look for an `ERROR` message (the workflow stopped) | the process owner [L] | — | **Beta** [L] (as a non-admin; the admin PAT gets 401: needs a user session). No v1 or dated version yet |
| Submit (launcher mode) | `PATCH /v2025/form-instances/{id}` (`application/json-patch+json`: `/formData`, `/state` `SUBMITTED`), `GET /v2025/form-instances/{id}` | Fill in and submit the Launcher form; repeat up to 3 times until SUBMITTED/COMPLETED or `formErrors`; `createdBy` is the run | USER | `[]` | GA [L] (as a non-admin, who can't *list* form instances: 403). PATCH goes through `fetch` with the SDK's token (the SDK has no PATCH) |
| Submit (test-endpoint mode) | `GET /v2025/workflows?limit=250` | Find the workflow by name (only when the runtime config has no ID) | ORG_ADMIN | `sp:workflow:read` | GA [L]; no deprecation header (v3 sent `Deprecation: 31 Mar 2027`) |
| Submit (test-endpoint mode) | `POST /v2025/workflows/{id}/test` `{input}` | **Start one run per part** (the workflow must be disabled) | ORG_ADMIN | `sp:workflow-execute:external` | GA [S] (same contract as v3; not called live, it starts a run) |
| Submit (test-endpoint mode) | `GET /v2025/workflow-executions/{id}` | Follow each run (not used in launcher mode) | ORG_ADMIN | `sp:workflow-execution:read` | GA [L]; same keys as v3, no deprecation header |
| Submit, My bulk requests | `GET /v2025/generic-approvals?limit=250&sorters=-createdDate&filters=requesterId eq "<me>"`, `GET /v2025/generic-approvals/{id}` | Find the part's approval (by `workflowExecutionId`, or by name), approver and decider | APPROVAL_OWNER | `idn:access-request-approvals:read` | GA [L] (filter and sort verified with the admin PAT) |
| My bulk requests | `GET /v3/access-request-status?requested-by=<me>&limit=250&offset=N&sorters=-created` | Requests carrying an INC | ORG_ADMIN; any user for their own [D] | `idn:access-request-status:read` | GA (legacy v3) [L] |
| **Approvals** | `GET /v2025/generic-approvals?mine=true&include-comments=true&limit=250&offset=N&sorters=createdDate&filters=status eq "PENDING" and type eq "ACCESS_REQUEST_APPROVAL"` | The caller's pending access-request approvals, with the item comment holding the INC | APPROVAL_OWNER | `idn:access-request-approvals:read` | GA [L] (as a non-admin) |
| **Approvals** | `POST /v2025/generic-approvals/{id}/approve` and `…/reject` `{comment}` | Decide as the caller (default path, ≤ 8 per second) | APPROVAL_OWNER | `idn:access-request-approvals:manage` | GA [L] (as a non-admin) |
| **Approvals** | `POST /v2025/generic-approvals/bulk-approve` and `…/bulk-reject` `{approvalIds ≤ 50, comment}` | Fast path, ORG_ADMIN only | ORG_ADMIN | `sp:approvals:write` | GA [L]. A non-admin gets 403 |
| **Approvals** | `GET /v2025/generic-approvals?limit=250&filters=approvalId in (…)` | Confirm every decision (50 IDs per call) | APPROVAL_OWNER | `idn:access-request-approvals:read` | GA [L] |
| **Approvals** | `GET /v2025/generic-approvals/{id}` | Who decided, only for IDs whose status changed although our call failed | APPROVAL_OWNER | same | GA [L] |

**What a non-admin can do in the plugin** (verified 2026-10-08 with two `sp:user` sessions; the page branches on
`context.user.capabilities.isOrgAdmin`):

| Feature | Non-admin | How |
|---|---|---|
| Submit | ✔ with the *Launcher Access* profile (`plugin.submit: "launcher"`) | launch, interactive-process blocks, own form instance |
| People search, pasted lists | ✔ | `/v3/public-identities` (identities, search and accounts are 403) |
| Catalog | ✔ | `requestable-objects?identity-id=<me>` (403 without it), `/v2025/entitlements` |
| "Already has it" | access profiles and roles ✔; entitlements skipped (said on the review step) | `requestable-objects?identity-id=<person>`; search is 403 and another person's `access-request-status` is 400 |
| My bulk requests | ✔ | `generic-approvals?filters=requesterId eq "<me>"`, `access-request-status?requested-by=<me>` |
| Approvals tab | ✔ | `generic-approvals` (APPROVAL_OWNER) |

With `plugin.submit: "test-endpoint"`, submitting needs ORG_ADMIN and the *New request* tab says so.

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

## 5. Workflow engine: steps the generated workflows use
Built by `core/bulkaccess/definitions.py`. **Library status** comes from `GET /v2025/workflow-library/{actions,operators,triggers}` [L].

| Step type (`actionId`, version) | Workflow and step names | What it does | Library status [L] |
|---|---|---|---|
| Trigger `idn:interactive-process-launched` (EVENT) | Launcher | Fires on any Launcher launch. Filtered with `filter.$: $[?(@.workflowId == '<own id>')]` | present, not deprecated |
| Trigger EXTERNAL (`idn:external-http`) | Plugin (test-endpoint mode only) | Never called externally: the plugin runs the **disabled** workflow through `/workflows/{id}/test` | present, not deprecated |
| `sp:interactive-form` v1 | Launcher: *Interactive Form* | Shows the form in the Launchpad (`formDefinitionId`, `interactiveProcessId`) | v1, not deprecated |
| `sp:interactive-message` v1 | Launcher: *Notify Pending*, `Reject …` | INFO or ERROR message back in the Launchpad | v1, not deprecated |
| `sp:get-identity` **v2** | both: *Get Requester*, *Get Approver* | Names and emails | v2 current (**v1 deprecated**, not used) |
| `sp:compare-strings` (StringEquals, StringMatches) | both: self-approval, approver in people (JSONPath filter), INC regex, duration and unit regex, `Approved?` | Checks before and after the approval | present |
| `sp:compare-boolean` (BooleanEquals) | Launcher: *Temporary?* (form TOGGLE) | Branch on the access type | present |
| `sp:define-variable` | Launcher: *Define Variable Access* | `removeDuration`, `accessLabel` and `partLabel`, with transform `sp:transform:replace:string` | present |
| `sp:update-variable` | Launcher: *Set Temporary Access*, *Set Part* | Builds `"30d"` and the label, with transform `sp:transform:concatenate:string`; copies the plugin's part label | present |
| `sp:generic-approval` v1 | both: *Bulk Approval* | **One approval** (`approvalType SINGLE`, `singleApproverCategory IDENTITY`), with a timeout, the action at timeout and a priority | v1, not deprecated |
| `sp:loop:iterator` v1 | both (live mode only): *Request Access* | One iteration per person (≤ 250), `context.$: "$"` | v1, not deprecated |
| `sp:access:manage` **v2** (inside the loop) | both: *Manage Access* | `GRANT_ACCESS`, all items, `removeDuration` (`""` = permanent), comment `INC… \| Bulk access request by … \| Approved by … \| … \| …` | v2 current |
| `sp:send-email` **v2** | both: approved, denied; plugin rejects | Requester (cc the approver), or `overrideRecipients` | v2 current (**v1 deprecated**, not used) |
| `sp:operator-success` and `sp:operator-failure` | both | End steps (failure carries `failureName` and `description`) | present |

**Deliberately not used:** `sp:create-approval-request` (the library now marks it **deprecated** [L], and it breaks
on one-item lists), and `sp:serial:iterator` (it silently stops after 50 items) [L].

## 6. Objects created, and the features they rely on
| Object | API | Features relied on |
|---|---|---|
| **Form definition** "<prefix> Bulk Access Request Form" | `/v2025/form-definitions` | SECTION. SELECT with an **INTERNAL IDENTITY** data source (`maximum` ≤ 30) and with **STATIC** options holding full `{id,type,name}` objects (`maximum` ≤ 25). TEXT with **REGEX** validation. TEXTAREA (no MAX_LENGTH). **TOGGLE**. **HIDDEN** (`partLabel`, set only by the plugin). `formConditions` HIDE effects [L] |
| **Workflow** "<prefix> Bulk Access Request" | `/v2025/workflows` | Enabled, Interactive trigger scoped to its own ID [L] |
| **Workflow** "<prefix> Bulk Access Request (Plugin)" (only with `plugin.submit: "test-endpoint"`) | `/v2025/workflows` | **Disabled**, External trigger, started through `POST /v2025/workflows/{id}/test` (formerly v3 [L]) |
| **Launcher** "<prefix> Bulk Access Request" | `/v2025/launchers` | `type: INTERACTIVE_PROCESS`, `reference: {type: WORKFLOW}`. ISC auto-creates an `assignedLaunchers` entitlement on the IdentityNow source [L] |
| **Access profile** "<prefix> Bulk Access Request - Launcher Access" | `/v3/access-profiles` | Wraps the `assignedLaunchers` entitlement. Requestable, with `approvalSchemes: [{approverType: MANAGER}]` or none [L] |
| **UI plugin** (alias `<prefix>-bulk-access`) | `/ui-plugins/v1` through `sail` | Slot `full-page`, `apiScopes: ["sp:scopes:all"]`, `restrictToUsers` (private = only the installer). Slots also carry `requiredCapabilities` [L] |
| **Access requests** (runtime) | Manage Access v2 in the workflow | Requester = **workflow owner**. Item approval schemes still apply [L] |
| **Generic approvals** (runtime) | `sp:generic-approval`, read through `/v2025/generic-approvals` | Named `Bulk access <INC>[ (k/n)]` [L] |

**`ENTITLEMENT` in the catalog** [L]+[S]: in the spec the `requestable-objects` `types` enum is only `ACCESS_PROFILE`
and `ROLE` (v3, v2025 and v2026). On the test tenant, `types=ENTITLEMENT` alone returns **400**, and the repeated form
(`types=ACCESS_PROFILE&types=ENTITLEMENT`) returns 200 **without any entitlement rows**, although 10 requestable
entitlements exist. So both deployments ask `requestable-objects` for access profiles and roles only, and read
requestable entitlements from `GET /v2025/entitlements?filters=requestable eq true` (plus `and name sw "<nameStartsWith>"`),
which returned exactly those 10 [L]. Entitlement rows have no `type`, so the code tags them `ENTITLEMENT`. See the risks in [API_CONTRACT_ALIGNMENT.md](API_CONTRACT_ALIGNMENT.md#5-risks-and-what-to-watch).

## 7. Scopes: what `sp:scopes:all` covers
Both the PAT and the plugin manifest ask for `sp:scopes:all`. The effective rights are still those of the user
(the PAT owner, or whoever is signed in to the plugin). For a least-privilege review, these are the spec scopes the
calls above need:

| Area | Scopes [S] |
|---|---|
| Identities and search | `idn:identity:read`, `sp:search:read`, `idn:accounts:read` |
| Forms | `sp:forms:read`, `sp:forms:manage` |
| Workflows | `sp:workflow:read`, `sp:workflow:manage`, `sp:workflow-execution:read`, `sp:workflow-execute:external` (test, cancel) |
| Launchers | `sp:launcher-admin:read`, `sp:launcher-admin:write`, `sp:launcher-admin:delete`, `sp:launcher-user:launch` (e2e) |
| Access model | `idn:entitlement:read`, `idn:access-profile:read`, `idn:access-profile:manage`, `idn:sources:read`, `idn:requestable-objects:read` |
| Access requests | `idn:access-request:manage`, `idn:access-request-status:read` |
| Approvals | `idn:access-request-approvals:read`, `idn:access-request-approvals:manage`, `sp:approvals:write` (bulk, ORG_ADMIN) |
| UI plugins (`sail`) | rights `idn:plugins-ui:create`, `:read`, `:update`, `:delete` [D] |
