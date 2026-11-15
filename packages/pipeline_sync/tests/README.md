# Pipeline provider acquisition tests

Run `python3 -m unittest discover -s packages/pipeline_sync/tests -v`.

All provider replies, identities and tokens are fictional; sockets are intercepted.
Temporary SQLite stores test atomic authorized local publication, scope isolation,
revocation/expiry between network reads and publication, stale revisions, provider
failures, strict projection and existing pipeline API integration. Transport tests
cover pinned public DNS, TLS hostname, fixed GET paths, header-only credentials,
redirect/error/compression refusal and byte/schema limits. No live provider or
production compatibility is claimed. [Boundary](../README.md)


## Cross-I/O flow pin regressions

`test_flow.py` covers one-refresh realm/local-ID/issuer/subject/generation pins,
subject ABA and deactivate/reactivate, same-subject actor reassignment, fresh
policy realm replacement, before-first-read/after-list/multi-detail/empty-result/
after-final-detail boundaries, and a fresh subsequent call. Scoped revision cases
cover role reduction/restore, synthetic membership revoke/regrant, both module
lifecycle cycles and workspace suspend/restore. Unrelated identity/membership
changes remain allowed. No remote pagination support is invented: the protocol
still reads one bounded list and its details; existing API offset/task pagination
is a separate snapshot consumer.

Actual temporary SQLite concurrency uses events/barriers, not sleeps: authority
changes while final publication waits for a writer reservation are revalidated;
two refreshes have one CAS winner and one success audit. Expiry after persistence
and an audit-insert failure roll back the resource and audit atomically.

The shared `workspaces/tests/identity_fixture.py` bypasses the closed guard only
inside disposable test transactions. A direct test-only membership restoration
isolates a future explicit regrant, which ordinary membership edits cannot do.
Neither fixture is imported by runtime code. Tests explicitly retain both service
and SQL identity-mutation denial. This is not real-provider, production or
whole-backup-rollback validation.
