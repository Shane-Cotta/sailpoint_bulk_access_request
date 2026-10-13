# Requirements checklist: is this tenant ready for Bulk Access Request?

Run this against a customer's production ISC tenant before installing. Each item has a one-line check: an API
**GET** (read-only; use an ORG_ADMIN PAT), a UI location, or both. **A** = Launcher deployment, **B** = UI plugin
deployment. The evidence tags ([L] live, [S] spec, [D] docs) follow [API_INVENTORY.md](API_INVENTORY.md). Version
and deprecation details are in [API_CONTRACT_ALIGNMENT.md](API_CONTRACT_ALIGNMENT.md).

> Base URL: `https://<tenant>.api.identitynow.com`. Expect `200` unless noted. Paths are the per-service GA paths
> (`/<service>/v1`) the solution uses since 2026-10-13.

**Since 2026-10-13 there is no bulk approver.** Each requested item is approved the way it's set up in SailPoint, and
items with no approval of their own are granted right away. The plugin files the access requests as the signed-in user
(`plugin.submit: "direct"`, the default) or, as a fallback, through the Launcher (`"launcher"`).

## 1. ISC features and modules
| ✔ | Requirement | For | How to check |
|---|---|---|---|
| ☐ | **Workflows** available | A (and B in launcher mode) | `GET /workflows/v1?limit=1` · UI: Admin → Workflows |
| ☐ | Workflow **actions** in the library: `sp:access:manage` **v2**, `sp:loop:iterator` v1, `sp:get-identity` v2, `sp:send-email` v2, `sp:interactive-form` v1, `sp:interactive-message` v1 | A | `GET /workflow-library/v1/actions?limit=250`: each `id` and `versionNumber` is present with `deprecated: false` [L] |
| ☐ | Workflow **operators**: `sp:compare-strings`, `sp:compare-boolean`, `sp:define-variable`, `sp:update-variable`, `sp:operator-success`, `sp:operator-failure` | A | `GET /workflow-library/v1/operators` |
| ☐ | Workflow **trigger** `idn:interactive-process-launched` | A | `GET /workflow-library/v1/triggers?limit=250` |
| ☐ | **Forms** (custom forms) | A | `GET /form-definitions/v1?limit=1` · UI: Admin → Forms |
| ☐ | **Launchers and Launchpad** | A | `GET /launchers/v1?limit=1` (returns `{items:[]}`) · UI: Home → Launchpad |
| ☐ | ISC **auto-creates the `assignedLaunchers` entitlement** for a Launcher (IdentityNow source). The *Launcher Access* profile that wraps it is also the plugin's submit gate | A, B | After install: `GET /entitlements/v1?filters=value eq "<launcherId>"` returns `attribute: assignedLaunchers` |
| ☐ | **UI Plugins** turned on and **licensed** (`idn:ui-plugins` or `idn:ui-plugins-author`). Experimental and gated [D] | B | `GET /ui-plugins/v1` with header `X-SailPoint-Experimental: true` → 200 (not "not enabled for this tenant") · `sail ui-plugins list` |
| ☐ | **Access requests API** for users: `POST /access-requests/v1` with up to 10 `requestedFor`, a per-item `comment` and `removeDate` (all three item types) | B (direct) | A dry-run is not enough: as a non-admin test user, one request for a test identity and a harmless item → `newRequests[]` [L, non-admin] |
| ☐ | **"Request for others"** is allowed for everyone (`requestOnBehalfOfConfig.allowRequestOnBehalfOfAnyoneByAnyone`) | B (direct) | `GET /access-request-config/v1` · UI: Admin → Global → Access Request Settings. When it's off, `plugin/install.py` installs the plugin in launcher mode (it needs deployment A) |
| ☐ | **Generic Approvals** (unified Approvals service), including access-request approvals as `type: ACCESS_REQUEST_APPROVAL` | B (Approvals tab), `status` | `GET /generic-approvals/v1?limit=1` · with an access request pending: `…?filters=type eq "ACCESS_REQUEST_APPROVAL"` returns it · UI: Home → Approvals |
| ☐ | **Temporary access:** Manage Access v2 `removeDuration` (`h`, `d`, `w`, `M`; `""` = permanent) for A; `removeDate` on `POST /access-requests/v1` for B | A, B | A live test on a test identity with 1 day: `GET /access-request-status/v1?requested-for=<id>` shows `removeDate` ≈ +1 day [L] |
| ☐ | **Request Center** has requestable, **enabled** access profiles or roles, or **requestable entitlements** | A, B | `GET /requestable-objects/v1?types=ACCESS_PROFILE&types=ROLE&limit=5` is not empty · entitlements: `GET /entitlements/v1?filters=requestable eq true&limit=5` is not empty (`requestable-objects` never returns entitlements [L], so the tool reads them here) · UI: Request Center |
| ☐ | **Item approval schemes** are what the business expects: they are now the **only** approval (one per person and item). Items with **no** scheme are granted at once: the installer lists them, the plugin labels them *"No approval: granted right away"* and warns on the review step | A, B | `python bulkaccess.py apply --dry-run` prints how many catalog items have no approval · per item: `GET /access-profiles/v1/{id}` or `/roles/v1/{id}` → `accessRequestConfig.approvalSchemes`; entitlements: `GET /entitlements/v1/{id}/entitlement-request-config`, falling back to `GET /access-request-config/v1` → `entitlementRequestConfig.grantRequestApprovalSchemes` [L] · UI: Admin → Access Profiles → Access Request settings |
| ☐ | **Email** delivery works for the Launcher workflow's emails (*Requested*, *Not requested*) | A | UI: Admin → Global → Email configuration (or the tenant's custom sender) · a dry-run with `notifications.overrideRecipients` set to a test inbox. (Direct plugin requests send no email of their own; ISC's usual access request notifications apply) |
| ☐ | Identities have an **email** attribute (the requester, for the Launcher's emails) | A | `GET /identities/v1/{id}` → `attributes.email` |

## 2. Identities and user levels
| ✔ | Requirement | For | How to check |
|---|---|---|---|
| ☐ | **Installer PAT owner is ORG_ADMIN.** It owns every object created and is the **requester** of every request the Launcher workflow files | A, B | The token's `authorities` claim includes `ORG_ADMIN` · UI: Admin → Identities → <user> → User Levels |
| ☐ | Recommended: a **dedicated service identity** as `owner`, one that owns or approves no items. Otherwise ISC escalates such item approvals (on Launcher requests) to an admin [L] | A | `python bulkaccess.py show-config` prints a note while `owner` is null |
| ☐ | **Plugin submitters hold the *Launcher Access* profile**, in both submit modes. **No ORG_ADMIN** is needed: the page checks the profile with `GET /requestable-objects/v1?identity-id=<me>&filters=id eq "<profile>"` (`requestStatus: ASSIGNED`) [L, non-admin], then files the requests (direct) or drives the Launcher (launcher) with the user's own session | B | `show-config` *Submit:* line · as a non-admin without the profile, a submit stops with *"To submit, you need '<prefix> Bulk Access Request - Launcher Access'. Request it in the Request Center."* |
| ☐ | **Direct mode:** the requester is the **signed-in user**: they see the requests in *My Requests* and the plugin's *My bulk requests*, and an item they'd approve themselves goes to an admin (ISC's own rule) [L] | B | A live test request as a non-admin, then *My Requests* |
| ☐ | **Launcher mode** needs **deployment A installed** (the plugin installer looks its Launcher up by name and writes its ID into the runtime config) and `/beta/interactive-processes/{id}/blocks` reachable for users [L, non-admin] | B (launcher) | `show-config` · a dry-run submit as a non-admin holder of the profile |
| ☐ | The *Launcher Access* profile is **requestable in the Request Center** by submitters (no segment hides it), auto-approved (`access.launcherApproval: "NONE"`) or approved by the manager (`MANAGER`, the default: submitters need a manager) | A, B | UI as a test user: Request Center → search *"<prefix> Bulk Access Request"* lists it · `GET /access-profiles/v1?filters=name eq "<prefix> Bulk Access Request - Launcher Access"` → `requestable: true`, `accessRequestConfig.approvalSchemes` |
| ☐ | **Item approvers** (owners, managers, governance-group members) need nothing extra for ISC Approvals → Access Requests. To use the plugin's **Approvals** tab they only need to see the plugin (§4); the spec's user level for generic approvals is APPROVAL_OWNER [S] | A, B | Signed in as an approver: the Approvals tab lists their items · `GET /generic-approvals/v1?mine=true` as that user |
| ☐ | **Launcher users** hold the *"<prefix> Bulk Access Request - Launcher Access"* profile (requested, manager-approved or granted) | A | The user sees the Launcher in their Launchpad · `GET /access-request-status/v1?requested-for=<id>` shows the grant |
| ☐ | **People** are correlated identities. The Launcher picker only matches **complete usernames** | A | UI: Launchpad form · plugin: paste usernames, emails or IDs |

## 3. API clients, PATs and scopes
| ✔ | Requirement | For | How to check |
|---|---|---|---|
| ☐ | A **PAT** (not an API client without a user): ISC needs user context for these calls [D] | A, B | UI: Preferences → Personal Access Tokens. At most 10 per user [D] |
| ☐ | PAT scope **`sp:scopes:all`**, or at least the scopes in [API_INVENTORY.md §7](API_INVENTORY.md#7-scopes-what-spscopesall-covers) | A, B | `POST /oauth/token` succeeds, and `python bulkaccess.py status` shows `[ok]` |
| ☐ | Installer user holds the **UI-plugin rights** `idn:plugins-ui:create`, `:read`, `:update`, `:delete` [D] | B | `sail ui-plugins list` works. `create` and `upload` report the missing right otherwise |
| ☐ | Plugin manifest `apiScopes: ["sp:scopes:all"]` is accepted | B | `GET /ui-plugins/v1` (experimental header) → plugin `apiScopes` |
| ☐ | Network path to the API host allows the PAT flow (proxy, or Cloudflare rejecting the default Python User-Agent with error 1010) | A, B | `python bulkaccess.py status` (sends `User-Agent: bulk-access-request/1.0` and honours `HTTPS_PROXY`) |

## 4. Tenant settings
| ✔ | Requirement | For | How to check |
|---|---|---|---|
| ☐ | **Plugin visibility:** private by default (the slot's `restrictToUsers` = the installer). For requesters and the Approvals tab, make it public (`plugin.public: true`) or add the users | B | `GET /ui-plugins/v1` (experimental header) → `slots[].restrictToUsers`. `requiredCapabilities` should be empty |
| ☐ | **Navbar item** for the plugin | B | UI: Admin → Global → System Settings → Customize Navbar → Custom Item → Destination: Plugin |
| ☐ | **Authentication profiles:** pass-through or SSO-only login can stop testers from signing in as a non-admin requester or approver | A, B | UI: Admin → Global → Security Settings → Service Provider / Authentication |
| ☐ | **Access request segments** don't stop the requester from requesting the catalog items for the people: the **signed-in user** in direct mode, the **workflow owner** for Launcher runs [D] | A, B | UI: Admin → Access → Segments · a live test request |
| ☐ | **Workflow daily execution thresholds** fit the volume (one run per Launcher form, ≤ 250 people each) | A | `GET /workflows/v1/{id}` → `dailyExecutionCount*` fields [L] |
| ☐ | **Test safety:** `mode: dry-run` and `notifications.overrideRecipients` until sign-off | A, B | `python bulkaccess.py status` shows `installed mode=dry-run` |

## 5. Limits the design relies on
| ✔ | Limit | Value | Where it matters | Evidence | How to check |
|---|---|---|---|---|---|
| ☐ | Form SELECT selections | **30** | Launcher people picker | [L] | The form refuses a 31st person |
| ☐ | Items per request | **25** | `catalog.maxItems` | [S] (access-requests: 25 entitlements) | — |
| ☐ | Recipients per access request | **10** | The plugin sends 10 people per `POST /access-requests/v1`; the workflow loops over people, one request each | [S] + [L] | — |
| ☐ | Loop (`sp:loop:iterator`) items | **250** (hard, fails above) | One Launcher run covers ≤ 250 people; in launcher mode the plugin sends parts | [L] | Execution history of a 250-person run |
| ☐ | Serial loop | stops silently at **50** | Not used | [L] | — |
| ☐ | Generic bulk approve, reject or cancel | **50** IDs, ORG_ADMIN only | Approvals tab fast path | [L] | — |
| ☐ | API rate limit | **100 per 10 s** per client_id and API version (everything is on `/v1`: one bucket) | Approvals tab (≤ 8/s), direct submit (3 requests in flight), people resolution | [D] | 429 + `Retry-After` |
| ☐ | Access request comment | ≤ 4000 characters; the tool caps the justification at **1000** for readability | The bulk comment on every request | [L] | — |
| ☐ | Workflow executions kept | 90 days | Audit of Launcher runs (`Execution History`) | [S] | — |

## 6. Tooling (the installer's workstation or CI)
| ✔ | Tool | Version | For | How to check |
|---|---|---|---|---|
| ☐ | Python | **3.10+**, standard library only | A, B | `python --version` |
| ☐ | Node.js | 22+ (SailPoint recommends 24+ for UI plugins [D]) | B | `node --version` |
| ☐ | npm | **≥ 11.12** (`npx -y npm@11`) | B | `npx -y npm@11 --version` |
| ☐ | SailPoint CLI `sail` | **≥ 2.7.0**. Never run it with `--debug`, which prints tokens | B | `sail --version` |
| ☐ | Package registry access (npmjs) for `npm install` and `npm run build` | — | B | `(cd plugin && npx -y npm@11 install)` |

## 7. Network and CSP
| ✔ | Item | Detail |
|---|---|---|
| ☐ | Outbound HTTPS from the installer to `https://<tenant>.api.identitynow.com` | `HTTPS_PROXY` / `NO_PROXY` are honoured. Nothing else is contacted at install time (npm registry aside) |
| ☐ | Plugin runtime makes **no third-party calls** | All API traffic goes through the plugin SDK to the tenant. The manifest's `contentSecurityPolicies` is empty. The dev CSP is `connect-src 'self'` |
| ☐ | Plugin assets are hosted by SailPoint (immutable, behind a CDN [D]) and rendered in an iframe slot (`full-page`) | Nothing to host for the customer |
| ☐ | Non-admin submitters need **no customer backend**: the plugin files the requests (direct) or drives the Launcher (launcher) with the user's own session. In launcher mode only, check that the API's CORS allows the plugin origin's `PATCH /form-instances/v1/{id}` (sent with `fetch`, because the SDK has no PATCH) | A test submit from the plugin as a non-admin |

## 8. Final smoke test (test identities and a harmless access profile)
1. `python bulkaccess.py apply --config config/<tenant>.json --dry-run`: review the payloads.
2. `apply --grant me --deploy`, then `status`: everything says `[ok]`.
3. `python launcher/e2e.py … --items "<test item>" --scenario request`, then `--scenario bad-duration` (stopped before anything is requested).
4. As a non-admin test user: request *Launcher Access* in the Request Center; it appears in their Launchpad.
5. As that user: a dry-run submit from the plugin (*"Dry-run mode: everything checks out, but nothing was requested."*),
   then, in live mode with a harmless test item, a real one: the requests appear at once, in the user's name, on
   *My bulk requests* and in *My Requests*. Nothing appears in **Approvals → Other**.
6. An item with an OWNER scheme: its owner's approval appears in **Approvals → Access Requests** and on the plugin's
   Approvals tab; approving it completes the request. An item with no scheme shows the warning and is granted at once.
7. Leave the installation in `mode: dry-run` until sign-off.

## 9. ISC UI behaviours to compare (seen on a live tenant, 2026-10-08 and 2026-10-13)
These are ISC's own screens, not the tool's. Check that the target tenant shows the same, and tell users if it doesn't.
The full lifecycle with screenshots is in [END_TO_END_WALKTHROUGH.md](END_TO_END_WALKTHROUGH.md).

| ✔ | Behaviour | Who notices | How to check |
|---|---|---|---|
| ☐ | Once *Launcher Access* is granted, the Launcher appears in the user's **Launchpad** within about a minute | Requester | UI: Launchpad |
| ☐ | Item approvals appear in **Approvals → Access Requests**, **one card per person and item**, titled **"Grant: <item>"**, or **"Modify: <item>"** when the person already has the item (for example only the end date changes). *Requested by* = the **signed-in requester** (direct) or the **workflow owner** (Launcher runs) | Item approvers | UI: Approvals → Access Requests |
| ☐ | Item approvals for different people can arrive **1–2 minutes apart** | Item approvers | Refresh after a live run |
| ☐ | **Direct mode:** the requester's **Request Center → My Requests** and the plugin's **My bulk requests** list every request, with who it's waiting for | Requester | UI as the requester after a live run |
| ☐ | **Launcher runs:** the requester's lists show **no** item requests (they're filed under the workflow owner); the workflow owner's **My Requests** lists them: **Completed** once provisioned, **Pending** while a source needs manual provisioning (for example a manual work item) | Requester, admin | UI as the requester and as the workflow owner |
