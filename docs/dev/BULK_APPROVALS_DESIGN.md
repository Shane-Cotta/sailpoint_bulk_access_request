# Design: bulk approvals for item approvers

**Status:** 2026-10-08. Phase 0 (live checks as a non-admin) done; Phase 1 (core rules and config) and Phase 2 (the
plugin's Approvals tab) built on `feature/bulk-approvals-core` / `feature/bulk-approvals-ui`. Phase 3 open.

## The problem
A bulk request has one *bulk approver*. Once they approve, the workflow submits a normal access request for every person.
Each item's own approval scheme (owner, manager, source owner, governance group) **still applies** to every one of those
requests. So 1,000 people × 1 owner-approved item means 1,000 approvals for the owner.

ISC gives ordinary approvers no bulk action. Their **Approvals** page shows one card at a time, each with its own
Approve or Deny. Only the admin page (*Approvals Administration*) has checkboxes with Approve, Cancel and Reassign,
and approving there records the admin as overriding the owner.

**Goal:** an item approver decides everything one bulk request (one INC) created for them in one action. The approval is
recorded **as that approver's own decision**.

## Verified live (2026-10-08, demo tenant)
| Fact | Result |
|---|---|
| Item approval schemes on workflow requests | **Applied.** A live bulk run (INC9967647, 3 people, an access profile with `approvalSchemes: [{approverType: OWNER}]`) created 3 approvals (`scheme ACCESS_PROFILE_OWNER`), all assigned to the non-admin owner, not forwarded. |
| Requester of workflow requests | Always the **workflow owner** (the PAT user), not the person who filled in the form. |
| Workflow owner is also the approver | ISC **escalates** that approval to an admin ("…because the Identity X is the Requester"). It doesn't approve it automatically. |
| Access-request approvals in the unified API | `GET /v2025/generic-approvals` returns them as `type: ACCESS_REQUEST_APPROVAL`, with `requestee`, `requestedTarget`, `assignedTo`, `approvalConfig.serialChain`, `dueDate` and `referenceData[type=accessRequestId]`. |
| INC on those approvals | In `comments[]`, but **only with `include-comments=true`** (they're left out without it). |
| Comment text search | Not possible (`search co` returns 0, and `comments` isn't a filter field). **Group by INC in the page.** |
| `mine=true` | Returns only the caller's approvals (an admin with no parameters gets the whole org). For a **non-admin**, no parameters already returns only their own; `approverId=<someone else>` → 400 "approverId must match the calling user's identity ID". |
| `include-comments=true` quirk | Rows then carry `comments[]` but **no `assignedTo`**; without it, `assignedTo` but no comments (also for a non-admin). So rely on `mine=true`, never on `assignedTo`. |
| `generic-approvals/bulk-approve` / `bulk-reject` | `{approvalIds[], comment?}`, **max 50**, returns `202 {}`. **Unknown IDs are accepted too**, so a 202 proves nothing. **403 for a non-admin, even on their own approvals.** |
| `access-request-approvals/bulk-approve` | Max 50, ORG_ADMIN or `idn:access-request-administration:write`. v3 `/access-request-approvals/pending` without `owner-id` → 400 "owner-id fields must be specified". |
| Single approve / reject as a non-admin | `POST /v2025/generic-approvals/{id}/approve` → 200 with the approval; `/reject` → **also 200 with the approval** (not 204 as the spec says). Audit: `approvedBy`/`rejectedBy` = the caller, `actionedAs [{type: ACCESS_PROFILE_OWNER}]`; the access request's `approvalDetails` show *reviewedBy* = the caller. |
| Someone else's approval, as a non-admin | `POST …/{id}/reject` → **403**, nothing changed. The server enforces "own approvals only" for non-admins. |
| Confirming | The list (also with `filters=approvalId in (…)`) has `status` but **empty `approvedBy`/`rejectedBy`**; only the single `GET /v2025/generic-approvals/{id}` names the decider. |
| Rate limit | 100 requests per 10 s per client and API version, then 429 with `Retry-After`. The plugin SDK doesn't expose response headers, so the page backs off instead of reading `Retry-After`. |

From the docs, **not yet tested live**:
- Governance-group approvals go to every member ("Multiple"). The first decision removes the approval from the other members' lists.
- `approvalConfig.serialChain` on list rows (the sample rows captured in Phase 0 had none; the page shows schemes when present).

## Design

### Where it lives
A new **Approvals** tab in the existing UI plugin, next to *New request* and *My bulk requests*.
- **Submitting** a bulk request still needs ORG_ADMIN, because it uses the workflow test endpoint.
- **The Approvals tab** works for anyone who can see the plugin. It only ever reads and decides the signed-in user's own approvals.

**Visibility:** each plugin slot has `restrictToUsers[]` (identity IDs; empty means everyone). Options:
1. Make the plugin public. Everyone sees it; non-admins get the Approvals tab by default, and *New request* shows the
   existing ORG_ADMIN banner and points them to the Launcher.
2. Keep it private and add item owners to `restrictToUsers`. That's a manual list; an installer helper could fill it from
   the owners of the catalog items.

### Data
```
GET /v2025/generic-approvals?mine=true&include-comments=true&limit=250&offset=…
    &filters=status eq "PENDING" and type eq "ACCESS_REQUEST_APPROVAL"
    &sorters=createdDate
```
- **Paging:** page through 250 at a time, stopping at a cap (`approvals.maxRows`, default 5,000).
- **Parsing:** `parseBulkComment()` reads each row's comments. It is a new shared rule in `rules.py` and `rules.ts`.
  - The format is `INC… | Bulk access request by … | Approved by … | <access> | <justification>`.
  - The justification is everything after the 4th separator, since it may contain `|`.
- **Grouping:** by INC. Rows without a bulk comment go in an *Other* group, hidden by default.
- **Group card fields:** INC, number of approvals / people / items, requester, bulk approver, access label, the schemes
  (from `serialChain[].identityType`), the oldest created date and the earliest due date.

### Deciding
Approve all or Deny all for an INC, minus any rows excluded in the drill-down. One comment covers all of them; it's
required to deny.

1. **Default path (everyone):** single `POST /v2025/generic-approvals/{id}/approve` or `/reject` with `{comment}`.
   - Up to `approvals.concurrency` calls at a time (default 4), held under ~8 requests per second.
   - A 429 or 5xx is retried with a growing back-off (the SDK hides `Retry-After`).
   - 1,000 items take about 2 minutes, with progress shown.
2. **Fast path (ORG_ADMIN only; non-admins get 403):** `generic-approvals/bulk-approve` or `bulk-reject` in batches of
   ≤ 50, when `useBulkEndpoint` is `always`, or `auto` and the user is ORG_ADMIN. A 401/403 switches the rest to the default path.
3. **Confirming:** neither path is trusted on its own. The page re-reads the IDs with `filters=approvalId in (…)` (50 per
   call; rounds after 1, 2, 4 and 8 s) and reports the result for each one:
   - **decided by me**: the status we sent and our call succeeded. Only when the status changed although our call failed
     does the page call the single GET to read the decider (the list leaves it out);
   - **decided by someone else**: a governance-group colleague or an admin. Not an error;
   - **still pending**: can be retried;
   - **failed**: can be retried, with the reason.
4. **Serial chains:** approving step 1 can create a new approval for step 2. If that's assigned to me, it shows up in the next load.

### Guard rails
- The page only acts on rows where the signed-in user is the approver (or a member of the approving governance group).
  This holds **for admins too** (`mine=true`). *Not built:* an admin switch "Show everyone's pending (view only)".
- The confirm dialog repeats the INC and the counts. The action can't be applied across INCs.
- The *Other* group (no bulk comment; only with `showOther`) is view-only: those approvals are unrelated, so deciding
  them together would be an action across requests.
- Demo mode gets fixtures and scenarios (`approvals`, `approvals-partial`) so the UI can be built without a tenant.

### Config (`config/<tenant>.json`, loaded only by `core/bulkaccess/config.py`)
```json
"approvals": { "enabled": true, "concurrency": 4, "useBulkEndpoint": "auto", "maxRows": 5000,
               "showOther": false, "denyCommentRequired": true }
```
- These are passed to the plugin through the generated `public/bulk-access.config.json`.
- `show-config` warns when approvals are on but the plugin is private and `restrictToUsers` is empty.

### Request-side changes (fewer approvals, less confusion)
- **Dedicated service identity as workflow owner** (`owner` in the config). If the owner is a person who also approves
  items, those approvals are escalated to an admin. `show-config` should warn about this.
- **Email wording:** "Approved" means approved by the bulk approver. Add: "Item owners may still need to approve; they
  can use the Approvals tab." The bulk approval's description should say the same.
- **Optional, phase 3:** `approval.bulkApprover: "skip"`, which drops the bulk step and relies only on the item
  schemes. This is a governance decision and stays off by default.

## Files
- **Plugin:**
  - **new:** `bulk/approvals.ts` (pure grouping and filters), `bulk/approvals-store.ts` (modelled on `request-store.ts`:
    signals, polling, generation guard) and `features/approvals/*`;
  - **changed:** `bulk/nav.ts` (`TabId` + `'approvals'`), `app.ts` and `app.html` (third tab, banner only on *New
    request*), `bulk/bulk-api.service.ts` (`pendingAccessApprovals()` and `decide()`), `bulk/errors.ts` (wording by
    context), `bulk/rules.ts`, `bulk/runtime-config.ts` and `demo/*`.
- **Core:** `rules.py` (`parse_bulk_comment`, `COMMENT_SEPARATOR`, and `definitions.py` builds the comment with that
  separator), `config.py` (the `approvals` block and derived values) and the example config.
- **Install:** `plugin/pluginlib.py` (runtime config, manifest description, `restrictToUsers`) and `plugin/install.py`
  (visibility warning).
- **Tests:**
  - **new:** `approvals.spec.ts` and `approvals-store.spec.ts`;
  - **extended:** `bulk-api.service.spec.ts`, `rules.spec.ts` / `rules.py` mirror cases, `core/tests/test_core.py`
    (the comment written by `definitions.py` reads back through the parser) and `plugin/tests`.
- **Docs:** README, USAGE (correct "one approval task"; add an item-approver section), plugin/README, CONTRACTS
  (new rules, config, the decide contract) and CLAUDE.md (verified facts above).

## Phases
- **Phase 0, live checks (needs a non-admin login):**
  1. As a non-admin, check that `mine=true` is scoped to them.
  2. Single approve and reject work as them, with their name in the audit trail.
  3. Generic bulk-approve as them: refused, or allowed?
  4. A governance-group approval: how `assignedTo` looks, and approval by one member.
  5. Throttling at concurrency 4.
  6. `restrictToUsers` makes the plugin visible to that user.
- **Phase 1:** core rules and config, runtime config, manifest and installer, Python tests.
- **Phase 2:** plugin API, store, component, demo mode, specs.
- **Phase 3:** docs, email wording, the workflow-owner warning, and optionally `approval.bulkApprover`.

## Test fixtures in the demo tenant (clean up afterwards)
- **Access profile:** "UCSF Bulk Owner-Approval Test" `5c9fc6cd83114a1c957a75928cd7159e` (owner Elliot.Reid, OWNER approval).
- **Pending approvals:** 3, for INC9967647 (Elena Petrova, Brenda Cooper, Beatriz Santos), assigned to Elliot.Reid.
  They expire with the 7-day removal date (2026-10-15).
- **Elliot.Reid** is a non-admin who has never registered. His email comes from a JDBC HR source, so it can't be changed
  for him alone through the API.
