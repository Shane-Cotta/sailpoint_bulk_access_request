"""Every ISC API path the tools call, in one place (mirrored by plugin/src/app/bulk/api-paths.ts).

All are SailPoint's per-service GA paths, `/<service>/v1`: the newest versions that are neither beta nor experimental.
The legacy `v3`, `beta` and `v202X` paths lose support in Q2 2028 (docs/sailpoint/API_CONTRACT_ALIGNMENT.md). Each
one was checked live without the experimental header; the exceptions are named below.
"""

ACCESS_PROFILES = "/access-profiles/v1"
ACCESS_REQUESTS = "/access-requests/v1"
ACCESS_REQUEST_CONFIG = "/access-request-config/v1"
ACCESS_REQUEST_STATUS = "/access-request-status/v1"
ENTITLEMENTS = "/entitlements/v1"
FORM_DEFINITIONS = "/form-definitions/v1"
FORM_INSTANCES = "/form-instances/v1"
GENERIC_APPROVALS = "/generic-approvals/v1"
IDENTITIES = "/identities/v1"
LAUNCHERS = "/launchers/v1"
REQUESTABLE_OBJECTS = "/requestable-objects/v1"
ROLES = "/roles/v1"
WORKFLOW_EXECUTIONS = "/workflow-executions/v1"   # history: /{id}/history-v2 (the v1 lineage of execution history)
WORKFLOWS = "/workflows/v1"

# Experimental (needs X-SailPoint-Experimental: true); there is no other API for UI plugins.
UI_PLUGINS = "/ui-plugins/v1"
