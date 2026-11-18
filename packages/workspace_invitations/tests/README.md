# Invitation acceptance tests

Run `python3 -m unittest discover -s packages/workspace_invitations/tests -v`
from the repository root. Test-only verifier and existing synthetic directory;
actual SQLite policy, invitation and audit transactions, no production credentials.

[Service contract](../README.md) · [Browser tests](../../../frontend/workspace-admin/tests/README.md)


`test_counter_bounds.py` adds synthetic `MAX`/`MAX-1` member re-enrollment and
invitation transitions, exact HTTP preconditions, corrupt captured counters,
two-event/epoch preflight, concurrent acceptance, restart and complete rollback
on final invitation expiry or the second audit failure. These do not create real
invitations, disclose identity data or enable identity lifecycle mutations.

`test_retry_namespaces.py` adds four focused methods using the shared disposable
`workspace_admin_api.tests.retry_namespace_fixture` through both real Bearer and
Cookie response boundaries. They cover inviter/workspace retry namespaces,
same-target business uniqueness versus a same-namespace body conflict, current
admin revocation/demotion without affecting another workspace's replay, and
same-key POST replay of accepted/invalidated history without granting membership.
Accepted-history replay preserves the revoked target membership; after inviter
demotion/restoration, invalidated-history replay preserves the original ID and
stored pending row with no target membership and remains unavailable for acceptance.
The other workspace stays pending. The inviter-axis success deliberately changes
the target to satisfy actionable-target uniqueness; the workspace-axis success
keeps the inviter, key and body identical, including
the explicitly authorized shared workspace. Exact response/row coordinates,
audit events, no pre-accept membership, and complete policy snapshots fence
retries and denials. The Cookie cases retain Origin/CSRF and their response
headers. Existing discovery includes this module; no workflow or identity
mutation change is required. This synthetic coverage is not live-provider or
production acceptance.
