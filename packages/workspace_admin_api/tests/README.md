# Administration contract tests

`test_application.py` uses the existing synthetic verifier/directory fixtures and
real temporary SQLite transactions. No live provider, real user or production
store is used. Run the command in the [package README](../README.md).

Coverage includes operator-only lifecycle metadata and creation, no implicit
operator membership, module admin isolation, catalog authority, archive/state
revisions, exact input rejection, durable idempotent replay/concurrency, current
authority rechecks and atomic policy/retry/audit rollback.

Browser composition and interaction coverage lives in
[workspace UI tests](../../workspace_ui/tests/README.md). Tests do not establish
deployment readiness or external provider compatibility.
