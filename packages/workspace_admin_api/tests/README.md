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

`test_retry_authority.py` covers both identity-owned retry endpoints, actor/target
ABA and independent realm mismatch, stale key conflicts without recreation,
strict bound-record validation, and current permission precedence. It exercises
legacy conflict tombstones, exact runtime schema checks, lost-authority legacy
row retention, per-stage conversion rollback, post-write rollback and a separate
process holding the actual SQLite writer lock across test-only identity ABA.
The simulator is only for disposable tests; identity mutation remains disabled.

## Initial-admin selection acceptance plan

This plan must be checked by existing CI on the exact reviewed head. No local
UT/build execution or successful acceptance is claimed by this document.

- GET only one explicit target under current operator authority; exact
  `initial_admin`/`precondition` fields, `ias1-` plus 64 lowercase hex, no query,
  list, external identity claims or exposed raw pins. Unknown and inactive
  targets share the same safe error. Lookup creates no policy/retry/audit data;
  cookie security-clock maintenance is not a business write.
- New-key POST requires one canonical `If-Initial-Admin-Match`. Missing, malformed
  and changed selections yield 428/400/412 with complete business/retry rollback.
  Cover target and actor ABA, foreign realm, inactive target, different ID,
  writer-lock waits and current-operator precedence. Keep actor's synthetic core
  grant current when testing the selection fence itself.
- Seed genuine old v2 records using the original three-field body digest. Exact
  replay remains valid without a header, after restart and after a lost response;
  an old client with an unused key cannot create. Existing-key handling precedes
  selection parsing. Changed body, stale actor/target provenance, NULL tombstones,
  corrupt records and missing results retain their original safe outcomes and
  never become misses, even with a fresh selection value.
- Multiple processes using the same key create one workspace, one current target
  membership and one success audit. The v2 schema, key namespace and digest remain
  unchanged. Replays neither re-enroll a member nor rebind grants.
- Bearer assertion expiry and cookie session/CSRF/Origin/current-identity checks
  remain independent gates, including final expiry and injected persistence/audit
  failure. The new header cannot supply authentication or operator authority.
- Reject merged/noncanonical header forms in the adapter and duplicate raw
  headers at the configured runtime boundary; other WSGI deployments retain their
  explicit raw-header normalization requirement.
- Run both management UI compositions and their existing interrupted-write,
  wrong-scope, no-bearer, stale-response and synthetic-fixture-cleanup checks. See
  the [browser plan](../../../frontend/workspace-admin/tests/README.md#initial-admin-selection-acceptance-plan).

No selection nonce table, credential, expiry, reservation or consumption is part
of this protocol. A normal restart need not invalidate an unchanged selection.
These cases do not open invitations/other identity selection, identity transitions,
legacy grant recovery, real login, whole-backup anti-rollback or production use.

## W13 namespace regressions

`test_retry_namespaces.py` uses the non-TestCase support module
`retry_namespace_fixture.py` and exercises the real in-process Bearer WSGI and
Cookie session/SQLite paths. Cookie responses use the existing session responder,
including its Cookie/Origin headers; normal POSTs supply the configured Origin
and current CSRF token. A fixture-level negative check retains both prerequisites.
This is not new TLS/browser or real identity-provider acceptance.

- Creation fixes action, scope, UUID and body while changing only the executing
  actor. Both current operators explicitly obtain their own initial-admin
  selection before writes and retain it for replay; POST never prepares one.
- Installation fixes actor, action, UUID and body across three spaces. Action
  isolation fixes actor, scope and UUID by installing in a real synthetic
  workspace named `platform`, matching creation's fixed scope. The route-specific
  bodies necessarily differ; both operations remain independently legal.
- One UUID also crosses administration and invitation tables without result or
  row adoption. The invitation namespace is separate, not a new generic action
  added to the administration table.
- A changed-body conflict and a committed local membership revoke leave other
  still-authorized namespaces replayable. Checks compare exact rows, results and
  policy/audit snapshots after the legitimate revoke, not before it. They do not
  promise availability across a corrupt schema/database or unchanged login-clock
  metadata.
- Six threads issue two copies of three different namespaces, using a barrier
  before any request/DB lock, prebuilt sessions/selections, bounded joins and
  collected failures. Each namespace must have one real object, retry row and
  success audit. This is not new cross-process or measured lock-wait acceptance.

The existing ABA, realm, restart, failure rollback, CSRF, selection and
cross-process tests remain unchanged. These focused cases add evidence only for
the existing creation/installation/invitation contracts, not generic business or
external-provider retry semantics or whole-W13 completion. The existing
`workspace-ui.yml` discovery includes this root without a workflow change.
Execution acceptance belongs to CI on the exact reviewed source; no local test
execution is claimed here.
