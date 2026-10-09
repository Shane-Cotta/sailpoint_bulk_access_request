# Requirements checklist: is this tenant ready for Bulk Access Request?

Run this against a customer's production ISC tenant before installing. Each item has a one-line check: an API
**GET** (read-only; use an ORG_ADMIN PAT), a UI location, or both. **A** = Launcher deployment, **B** = UI plugin
deployment. The evidence tags ([L] live, [S] spec, [D] docs) follow [API_INVENTORY.md](API_INVENTORY.md). Version
and deprecation details are in [API_CONTRACT_ALIGNMENT.md](API_CONTRACT_ALIGNMENT.md).

> Base URL: `https://<tenant>.api.identitynow.com`. Expect `200` unless noted. Paths are shown with the version the
> solution uses today.

## 1. ISC features and modules
| ✔ | Requirement | For | How to check |
|---|---|---|---|
| ☐ | **Workflows** available | A, B | `GET /v2025/workflows?limit=1` · UI: Admin → Workflows |
| ☐ | Workflow **actions** in the library: `sp:access:manage` **v2**, `sp:generic-approval` v1, `sp:loop:iterator` v1, `sp:get-identity` v2, `sp:send-email` v2, `sp:interactive-form` v1, `sp:interactive-message` v1 | A, B | `GET /v2025/workflow-library/actions?limit=250`: each `id` and `versionNumber` is present with `deprecated: false` [L] |
| ☐ | Workflow **operators**: `sp:compare-strings`, `sp:compare-boolean`, `sp:define-variable`, `sp:update-variable`, `sp:operator-success`, `sp:operator-failure` | A, B | `GET /v2025/workflow-library/operators` |
| ☐ | Workflow **triggers**: `idn:interactive-process-launched` (A), `idn:external-http` (B) | A, B | `GET /v2025/workflow-library/triggers?limit=250` |
| ☐ | **Forms** (custom forms) | A | `GET /v2025/form-definitions?limit=1` · UI: Admin → Forms |
| ☐ | **Launchers and Launchpad** | A | `GET /v2025/launchers?limit=1` (returns `{items:[]}`) · UI: Home → Launchpad |
| ☐ | ISC **auto-creates the `assignedLaunchers` entitlement** for a Launcher (IdentityNow source) | A | After install: `GET /v2025/entitlements?filters=value eq "<launcherId>"` returns `attribute: assignedLaunchers` |
| ☐ | **UI Plugins** turned on and **licensed** (`idn:ui-plugins` or `idn:ui-plugins-author`). Experimental and gated [D] | B | `GET /ui-plugins/v1` with header `X-SailPoint-Experimental: true` → 200 (not "not enabled for this tenant") · `sail ui-plugins list` |
| ☐ | **Generic Approvals** (unified Approvals service), including access-request approvals as `type: ACCESS_REQUEST_APPROVAL` | A, B | `GET /v2025/generic-approvals?limit=1` · with an access request pending: `…?filters=type eq "ACCESS_REQUEST_APPROVAL"` returns it · UI: Home → Approvals |
| ☐ | **Manage Access v2 with temporary access** (`removeDuration`: `h`, `d`, `w`, `M`; `""` = permanent) | A, B | Library check above (v2), then a live test on a test identity with `1d`: `GET /v3/access-request-status?requested-for=<id>` shows `removeDate` ≈ +1 day [L] |
| ☐ | **Request Center** has requestable, **enabled** access profiles or roles, or **requestable entitlements** | A, B | `GET /v3/requestable-objects?types=ACCESS_PROFILE&types=ROLE&limit=5` is not empty · entitlements: `GET /v2025/entitlements?filters=requestable eq true&limit=5` is not empty (`requestable-objects` never returns entitlements [L], so the tool reads them here) · UI: Request Center |
| ☐ | **Item approval schemes** are known (they still apply after the bulk approval; one approval per person and item) | A, B | `GET /v3/access-profiles/{id}` → `accessRequestConfig.approvalSchemes` · UI: Admin → Access Profiles → Access Request settings |
| ☐ | **Workflow test endpoint** allowed for disabled workflows (only with `plugin.submit: "test-endpoint"`: the plugin starts runs this way) | B | After install, a dry-run submit from the plugin creates an execution · UI: Admin → Workflows → "<prefix> Bulk Access Request (Plugin)" → Execution History |
| ☐ | **Email** delivery works for workflow emails (approved, denied, rejected) | A, B | UI: Admin → Global → Email configuration (or the tenant's custom sender) · a dry-run with `notifications.overrideRecipients` set to a test inbox |
| ☐ | Identities have an **email** attribute (requester, plus the approver for cc) | A, B | `GET /v2025/identities/{id}` → `attributes.email` |

## 2. Identities and user levels
| ✔ | Requirement | For | How to check |
|---|---|---|---|
| ☐ | **Installer PAT owner is ORG_ADMIN.** It owns every object created and is the **requester** of every workflow access request | A, B | The token's `authorities` claim includes `ORG_ADMIN` · UI: Admin → Identities → <user> → User Levels |
| ☐ | Recommended: a **dedicated service identity** as `owner`, one that owns or approves no items. Otherwise ISC escalates such item approvals to an admin [L] | A, B | `python bulkaccess.py show-config` prints a note while `owner` is null |
| ☐ | **Plugin submitters hold the *Launcher Access* profile** (`plugin.submit: "launcher"`, the default): the plugin launches the Launcher and submits its form as them, so **no ORG_ADMIN** is needed. This needs **deployment A installed in launcher mode** (the plugin installer looks its Launcher up by name and writes its ID into the runtime config) and `/beta/interactive-processes/{id}/blocks` reachable for users [L, non-admin] | B | `show-config` *Submit:* line · try a submit as a non-admin holder of the profile; someone without it gets *"To submit, you need '<prefix> Bulk Access Request - Launcher Access'. Request it in the Request Center."* |
| ☐ | The *Launcher Access* profile is **requestable in the Request Center** by submitters (no segment hides it), auto-approved (`access.launcherApproval: "NONE"`) or approved by the manager (`MANAGER`, the default: submitters need a manager) | A, B | UI as a test user: Request Center → search *"<prefix> Bulk Access Request"* lists it · `GET /v3/access-profiles?filters=name eq "<prefix> Bulk Access Request - Launcher Access"` → `requestable: true`, `accessRequestConfig.approvalSchemes` |
| ☐ | **Or, with `plugin.submit: "test-endpoint"`: plugin submitters are ORG_ADMIN.** `POST /v2025/workflows/{id}/test` requires it [S] | B | Banner on the plugin's *New request* tab · UI user levels |
| ☐ | **Item approvers** (owners, managers, governance-group members) can use the **Approvals** tab, with no admin rights needed. The spec's user level for generic approvals is APPROVAL_OWNER [S] | B | Signed in as an approver: the plugin opens on Approvals and lists their items · `GET /v2025/generic-approvals?mine=true` as that user |
| ☐ | **Launcher users** hold the *"<prefix> Bulk Access Request - Launcher Access"* profile (requested, manager-approved or granted) | A | The user sees the Launcher in their Launchpad · `GET /v3/access-request-status?requested-for=<id>` shows the grant |
| ☐ | **Bulk approver** is any identity other than the requester and the people on the request. **Needs nothing extra**: no profile, no plugin, no admin rights | A, B | Enforced by the form, the plugin and the workflow · the approval shows in their ISC Approvals → Other |
| ☐ | **Item approvers** need nothing extra for ISC Approvals → Access Requests; to use the plugin's Approvals tab they only need to see the plugin | A, B | See §4 *Plugin visibility* |
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
| ☐ | **Plugin visibility:** private by default (the slot's `restrictToUsers` = the installer). For the Approvals tab, make it public (`plugin.public: true`) or add the approvers | B | `GET /ui-plugins/v1` (experimental header) → `slots[].restrictToUsers`. `requiredCapabilities` should be empty |
| ☐ | **Navbar item** for the plugin | B | UI: Admin → Global → System Settings → Customize Navbar → Custom Item → Destination: Plugin |
| ☐ | **Authentication profiles:** pass-through or SSO-only login can stop testers from signing in as a non-admin approver or Launcher user | A, B | UI: Admin → Global → Security Settings → Service Provider / Authentication |
| ☐ | **Access request segments** don't stop the workflow owner (the requester) from requesting the catalog items for the people [D] | A, B | UI: Admin → Access → Segments · a live test request |
| ☐ | **Workflow daily execution thresholds** fit the volume (one run per part of ≤ 250 people) | A, B | `GET /v2025/workflows/{id}` → `dailyExecutionCount*` fields [L] |
| ☐ | **Test safety:** `mode: dry-run` and `notifications.overrideRecipients` until sign-off | A, B | `python bulkaccess.py status` shows `installed mode=dry-run` |

## 5. Limits the design relies on
| ✔ | Limit | Value | Where it matters | Evidence | How to check |
|---|---|---|---|---|---|
| ☐ | Form SELECT selections | **30** | Launcher people picker | [L] | The form refuses a 31st person |
| ☐ | Items per request | **25** | `catalog.maxItems` | [S] (v3 access-requests: 25 entitlements) | — |
| ☐ | Recipients per access request | **10** | Workflow loops over people, one request each | [S] + [L] | — |
| ☐ | Loop (`sp:loop:iterator`) items | **250** (hard, fails above) | One approval covers ≤ 250 people. The plugin sends parts | [L] | Execution history of a 250-person run |
| ☐ | Serial loop | stops silently at **50** | Not used | [L] | — |
| ☐ | Generic bulk approve, reject or cancel | **50** IDs, ORG_ADMIN only | Approvals tab fast path | [L] | — |
| ☐ | API rate limit | **100 per 10 s** per client_id and API version | Approvals tab (≤ 8/s), people resolution | [D] | 429 + `Retry-After` |
| ☐ | Approval name and comment | name ≤ 50 characters, comment ≤ 150 characters | `Bulk access <INC> (k/n)`, justification | project notes [L] | — |
| ☐ | Workflow executions kept | 90 days | Audit (`Execution History`) | [S] | — |

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
| ☐ | Non-admin submitters need **no customer backend**: in launcher mode the plugin submits through the Launcher with the user's own session. Only check that the API's CORS allows the plugin origin's `PATCH /v2025/form-instances/{id}` (sent with `fetch`, because the SDK has no PATCH) | A test submit from the plugin as a non-admin |

## 8. Final smoke test (test identities and a harmless access profile)
1. `python bulkaccess.py apply --config config/<tenant>.json --dry-run`: review the payloads.
2. `apply --grant me --deploy`, then `status`: everything says `[ok]`.
3. `python launcher/e2e.py … --scenario approve`, then `deny`, then `self` (dry-run).
4. As a non-admin test user: request *Launcher Access* in the Request Center; it appears in their Launchpad.
5. As that user: a dry-run submit from the plugin; the bulk approver approves it in **ISC Approvals → Other**.
6. As a non-admin item approver: the Approvals tab lists and decides their items (live mode, a test profile with an OWNER scheme).
7. Leave the installation in `mode: dry-run` until sign-off.

## 9. ISC UI behaviours to compare (seen on a live tenant, 2026-10-08)
These are ISC's own screens, not the tool's. Check that the target tenant shows the same, and tell users if it doesn't.
The full lifecycle with screenshots is in [END_TO_END_WALKTHROUGH.md](END_TO_END_WALKTHROUGH.md).

| ✔ | Behaviour | Who notices | How to check |
|---|---|---|---|
| ☐ | Once *Launcher Access* is granted, the Launcher appears in the user's **Launchpad** within about a minute | Requester | UI: Launchpad |
| ☐ | The bulk approval appears in **Approvals → Other** (a generic approval), not *Access Requests*, titled **"Grant: Bulk access INC…"**, with *Requested by* and *Recipient* = the requester | Bulk approver | UI: Approvals → Other |
| ☐ | Its drawer shows only the comment **"INC…: justification"**: ISC shows no people, items or access type there | Bulk approver | UI: open the card → Comments |
| ☐ | **Approve acts immediately** in that drawer (and on the card): ISC asks for no confirmation | Bulk approver | UI, with a dry-run request |
| ☐ | Item approvals appear in **Approvals → Access Requests**, **one card per person**, *Requested by* = the **workflow owner** (the installer's PAT user, or `owner`), titled **"Grant: <item>"**, or **"Modify: <item>"** when the person already has the item (for example only the end date changes) | Item approvers | UI: Approvals → Access Requests |
| ☐ | Item approvals for different people can arrive **1–2 minutes apart** | Item approvers | Refresh after a live run |
| ☐ | The requester's **Request Center → My Requests** does **not** list the item requests (they're filed under the workflow owner); the plugin's **My bulk requests** shows the approval status (non-admins) and the requests (admins) | Requester | UI as the requester after a live run |
| ☐ | The workflow owner's **My Requests** lists every item request: **Completed** once provisioned, **Pending** while a source needs manual provisioning (for example a manual work item) | Admin | UI as the workflow owner |
