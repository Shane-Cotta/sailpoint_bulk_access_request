"""Every ISC API path this tool calls, in one place.

All are SailPoint's per-service GA paths, `/<service>/v1`: the newest versions that are neither beta nor dated. The
legacy `v3`, `beta` and `v202X` paths lose support in Q2 2028. One exception: source apps are still experimental
(`/source-apps/v1` needs `X-SailPoint-Experimental: true`); nothing else lists or changes app owners.
"""

ACCESS_PROFILES = "/access-profiles/v1"
ACCESS_REQUESTS = "/access-requests/v1"
ENTITLEMENTS = "/entitlements/v1"
GENERIC_APPROVALS = "/generic-approvals/v1"
IDENTITIES = "/identities/v1"
IDENTITY_PROFILES = "/identity-profiles/v1"
PARAMETERS = "/parameter-storage/v1/parameters"
ROLES = "/roles/v1"
SOURCE_APPS = "/source-apps/v1"          # experimental
SOURCES = "/sources/v1"
TRIGGER_SUBSCRIPTIONS = "/trigger-subscriptions/v1"
WORKFLOW_EXECUTIONS = "/workflow-executions/v1"
WORKFLOW_LIBRARY = "/workflow-library/v1"
WORKFLOWS = "/workflows/v1"
WORKGROUPS = "/workgroups/v1"
