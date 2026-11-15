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
