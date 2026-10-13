/**
 * Every ISC API path the plugin calls, in one place (the Python tools' list is core/bulkaccess/api.py).
 *
 * All are SailPoint's per-service GA paths, `/<service>/v1`: the newest versions that are neither beta nor
 * experimental. The legacy `v3`, `beta` and `v202X` paths lose support in Q2 2028
 * (docs/sailpoint/API_CONTRACT_ALIGNMENT.md). The one exception is named below.
 */
export const API = {
  accessRequests: '/access-requests/v1',
  accessRequestStatus: '/access-request-status/v1',
  accounts: '/accounts/v1',
  entitlements: '/entitlements/v1',
  formInstances: '/form-instances/v1',
  genericApprovals: '/generic-approvals/v1',
  identities: '/identities/v1',
  launchers: '/launchers/v1',
  publicIdentities: '/public-identities/v1',
  requestableObjects: '/requestable-objects/v1',
  search: '/search/v1',
  workflowExecutions: '/workflow-executions/v1',
  workflows: '/workflows/v1',
  /** Beta: no GA path returns a Launcher run's blocks (its form). Only the Launcher submit mode needs it. */
  interactiveProcessesBeta: '/beta/interactive-processes',
} as const;
