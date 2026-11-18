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

## Interactive replacement gate (Linux CI, fabricated observations only)

The same unittest discovery command also runs the opt-in reviewed path:

- `test_reviewed_terminal.py` launches a disposable subprocess in a new POSIX
  session with its own owner-only Unix98 slave as its foreground controlling
  terminal. The test reads a size/time-bounded synthetic preview **only in
  memory**. It checks the complete projected bytes, old/removal counts, source,
  audience and redaction before replying. A separate viewer calls the unchanged
  PipelineApplication against the temporary SQLite file while the decision is
  pending and after commit. An outsider stays denied. Wrong or prior challenges
  and a child with no controlling TTY leave the old snapshot unchanged. An
  unsafe-mode PTY (group-writable permissions or character transformations)
  receives no preview bytes.
  No terminal transcript, decision, source payload, screenshot or credential
  goes to the runner log or an artifact. This demonstrates a kernel TTY path,
  not real human review, confidentiality of an arbitrary terminal, or permission
  to publish any real source. No test connects to an adapter or a provider.
- `test_reviewed_refresh.py` holds the decision at service boundaries to check
  denied roles, scope/identity/revision fences, no implicit network, empty or
  invalid observations, an independent competing write, monotonic expiry and
  snapshot/audit rollback. It injects separate post-persistence storage faults
  to prove both exact record bytes and audit are reread before commit. A
  mocked confirmation is **not** TTY acceptance.
- `test_review_decision.py` checks candidate byte identity and the fields bound
  to one decision with a fixed nonce. The independent PTY replay check uses two
  real fresh challenges. `_review_fixture.py` is fixture setup only; the
  synthetic operator grant does not come from the refresh caller.
