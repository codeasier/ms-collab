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

## Explicit recipient Review

`test_recipient_selection.py` adds fixed literal canonical-byte/hash coverage and
focused first-write selection tests. `selection_fixture.py` has no TestCase and
never fetches a proof as a side effect of create/replay. Each fresh successful
setup explicitly calls Review and passes the retained value; historical replay,
body conflicts and current-authority denials deliberately omit or corrupt it.
The pre-existing invitation/counter/namespace suites retain their assertions,
including accepted/invalidated W13 history, unchanged raw pins/expiry, membership
revisions and audit events. Their fresh setup calls alone now obtain a selection.

The new HTTP cases use disposable synthetic identities and the actual in-process
WSGI Bearer and Cookie transaction boundaries. Cookie uses
`SessionWorkspaceApplication._respond(dispatch=routes._dispatch)`, not the
inherited Bearer responder. Exact response/security headers, Origin/CSRF,
current/final authentication, W05 precedence, 428/400/412 no-write snapshots,
old-key compatibility, identity ABA, realm/actor/workspace/role separation,
identity-only stability and restart equality are covered. Test-only identity
manipulation uses the existing closed-foundation fixture, never a runtime route.
Review does not initialize or read invitation rows through its callback, but the
normal loader still enforces global foreign-key integrity. Policy snapshots stay
unchanged; advancing the Cookie login security clock is explicitly allowed.

The writer-wait case uses a real SQLite writer reservation and separate connection
in a POST thread. Its bounded barrier is before lock acquisition; the coordinator
observes the actual `BEGIN IMMEDIATE` attempt, commits the target ABA and joins the
caller. It uses no sleep and claims neither multiprocess nor browser evidence.
Existing concurrent-create, rollback, restart and invitation acceptance tests
remain the broader transaction gates. The supported runtime raw-parser suite also
checks duplicate selection headers and underscore aliases before WSGI merging.

Only text, AST, diff and hash checks are permitted during this increment's local
preparation. Hosted CI on the frozen exact source head must execute the tests;
this documentation is not a report of local or remote test success. Separate
real-browser HTTP and synthetic HTTPS journeys remain required, not live-provider
or production acceptance.
