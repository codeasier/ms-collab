# Principal authority foundation (closed identity administration)

This increment establishes persistent authority coordinates and conservative
consumer invalidation. It **does not enable identity provisioning, rebinding,
disable/reenable, directory synchronization or real GitCode login**. It is not a
claim of complete tenant isolation or a production migration.

## Code-level closed gate

Both `WorkspaceService.set_principal` and `SQLiteWorkspaceService.set_principal`
always raise `503 identity_transition_unavailable`, including trusted callers.
There is no enabling flag or callable transition implementation. SQLite guards
reject changes to principal ID, issuer, subject, activity, generation and authority
epoch; deletes and inserts/replacements after bootstrap are also rejected. Exact
schema validation requires those guards. Removing a guard makes normal operations
unavailable rather than enabling identity administration. Realm, migration marker
and policy epoch have corresponding immutability/monotonicity guards.

These guards are application integrity controls, **not a sandbox against code or
an administrator that can rewrite the database/schema or restore old backups**.
The test-only `workspaces/tests/identity_fixture.py` temporarily removes a guard
inside a disposable test transaction to simulate future authority transitions.
It deliberately retains memberships to isolate consumer-fence tests; it is not a
production transition algorithm and runtime modules may not import it.

## Persistent contract

- Each policy database has a random 128-bit `metadata.realm`, retained on normal
  reopen. A newly initialized/rebuilt database receives a different realm.
- `Principal.generation` and `Principal.authority_epoch` are exact integers in
  `1..9007199254740991`. New/legacy migrated principals start at 1. The authority
  epoch cannot exceed the current bounded global policy epoch.
- Identity-bound authority uses **realm + local principal ID + generation**, with
  issuer/subject checked as well. A local ID or matching restored subject alone
  is insufficient. Missing generation is never silently replaced with 1 on reads.
- Generation changes are not currently executable. A future transition must
  increase generation and global epoch atomically; A→B→A or inactive→active must
  not restore the older generation. No-op requests must not increment either.

## Login/session transaction boundary

Composing `SessionPolicyStore` binds its login-store object to one initialized
policy database. Every subsequent bound login operation opens **policy as main,
login as ATTACH**, in the same lock order used by protected requests. Both stores
must be local files on the same device with DELETE journals and FULL synchronous
mode; checks repeat under the writer reservation. No journal settings change.

An attempt captures realm and global policy epoch. External code exchange still
occurs without a database lock, after single-use consumption. Session issuance
then reloads the current principal under the joint transaction and rejects an
attempt begun before that principal's authority epoch, including an unobserved
A→B→A during exchange. An unrelated membership change alone does not invalidate
all attempts. The new session stores principal ID, generation and realm; every
inspection/CSRF/protected request checks them against current active identity.

Old unbound/legacy sessions or attempts retain NULL authority fields. They may
remain protocol records in a standalone login store, but cannot authorize the
composed workspace host. Partial authority fields, future epochs, a foreign realm
or a mismatched generation fail closed. A bound session cannot be reused through
an unbound login-store object. Compose the policy before starting a login; never
mint standalone sessions and later silently adopt them.

`revoke_subject` remains deletion of existing sessions only, not account disable
or an in-flight-login barrier. Clock high-water marks, expiry, single-use state,
CSRF, cookie recovery and default-disabled real provider behavior remain intact.
[GitCode acceptance](gitcode-login-acceptance.md) is a separate uncompleted gate.

## Explicit v1 → v2 upgrades (offline, operator-controlled)

Normal constructors reject v1. They never auto-upgrade or create a missing file.
The explicit helpers are `SQLiteWorkspaceService.migrate_v1(path)` and
`SQLiteLoginStore.migrate_v1(path, binding=..., clock=...)`. They are trusted local
library operations, **not HTTP routes, runtime auto-migrations or a deployment
command**. They must not run against production merely because this code exists.

1. Stop request-serving processes. Establish an authorized, protected backup and
   a rollback plan; login stores include sensitive short-lived verifier material.
   Do not put database files, sessions, verifiers or raw dumps in source control.
2. Under `BEGIN IMMEDIATE`, validate application ID, exact v1 core definitions,
   indexes/triggers and retained data. Known unrelated module tables are retained
   by the policy migration; modified core tables or unknown core triggers/indexes
   are rejected. Login has no extension tables requiring migration.
3. Execute each DDL individually in the transaction. Add policy realm/generation,
   a minimal immutable `authority_migrations` marker, and the closed guards; add
   nullable authority columns to login attempts/sessions. Validate v2 before
   committing its version. Fault-injection tests cover every migration DDL,
   retaining old columns/data/triggers/version on failure.
4. Reopen using v2, compose policy/login, and require a new verified login. Old
   rows are **not backfilled into current authority**. History fences without
   generation are hidden; legacy GUI snapshots require explicit new publication;
   legacy pending invitation digests are invalidated. Preserve retained records
   for separately authorized recovery rather than exporting or silently rewriting.

The policy marker records source/target version, principal count and generated
realm, with no credential, external identifier or operator claim. It is not a
full audit of identity transitions. V1 inactive principals that still have active
memberships/operator rows are preserved, but cannot be reactivated through any
supported path. A future transition must explicitly revoke those old grants.
Restoring a historical policy backup can restore old authority state; this
foundation does not provide an external anti-rollback ledger or tamper evidence.

## Consumer inventory and activation gates

This inventory is scoped to the implemented modules, not proof that every future
consumer is safe. A cookie UI uses the session fence, but that **does not upgrade
its underlying persisted local-ID ACLs**. New module integrations must be added
here before identity administration can open.

| Consumer | This increment | Remaining gate before executable identity transitions |
| --- | --- | --- |
| Login attempts and sessions; session-backed host/UI | Joint realm/epoch issuance and exact current ID/generation verification; legacy NULL denied in composed host. | Real provider bound-code acceptance remains separate. Backup rollback is not solved. |
| Saved assistant history and blocked ACP preflight | Persistent realm/generation fence; old records hidden, no automatic recovery; preflight reuses exact history fence. | Object-level lineage, executable runner isolation/authorization and recovery not implemented. |
| GUI observation snapshots | V2 audience includes realm and each reader generation; old snapshot reads denied until explicit newer publication. | Trusted publisher allowlist is still local IDs; upgrade publisher/configuration grants before identity administration. |
| Workspace invitations | Inviter and invitee digests include realm, ID, issuer, subject and generation; old digests never upgraded. | Actual directory transitions and identity-safe administrative workflow remain closed. |
| Artifact byte grants and streams | Persistent realm/generation grants, independent monotonic byte revision and reviewed-parent index pin; full flow pin checked through acquisition/output. [Contract](../packages/artifact_sync/GRANTS.md). | Real cloud acceptance and host resource recovery remain separate; regrant never recycles uncertain active leases. |
| Community reads/review/refresh/approve | [Bound persistent grants](../packages/community_api/GRANTS.md), retired-ID pins, monotonic grant revisions, approver generation/membership provenance and per-page actor/config guards. Known legacy grants require explicit CAS regrant that discards old content. | Management/template constraints remain additional gates, not substitutes for data-plane grants. Standalone provider calls do not claim workspace authorization; already dispatched I/O cannot be undone. |
| Artifact index/read/review/approve/refresh | Persistent role grants and approver/membership provenance; explicit destructive CAS regrant; admission-origin flow pin and before/after-page checks on both bearer and cookie paths. [Contract](../packages/artifact_sync/GRANTS.md). | Legacy payloads stay unavailable until explicit regrant; no actual identity mutation or live-provider acceptance is enabled. |
| Pipeline snapshots and acquisition | Reads use current workspace/module policy. | Bearer acquisition spans external I/O with configuration/resource revision pins, not a principal generation pin. |
| Research assets | Reads/publication use current workspace/module checks and fixed source binding. | Long-I/O acquisition's actor-equality pin must include realm/generation. |
| Memberships, operators, workspace creation idempotency | Existing role/lifecycle controls unchanged. | Every non-no-op identity transition must revoke all active memberships and operator membership, including reactivation of legacy inactive identities. Idempotency records keyed by local actor need binding-aware reuse semantics. |
| Responsibility owners/backups and UI hints | Existing explicit current-member validation unchanged. | Stored owner IDs and backup references need a deliberate identity transfer/reconfirmation policy, not implicit reassignment. |
| Community connection self-management and directory | Fixed installed template pins bind realm + local ID + issuer/subject + generation; missing/old pins fail closed. Current manager/admin and selected-member eligibility are rechecked. | Template constraints supplement the separate community data-plane grants above; they never upgrade legacy/stale data pins. No automatic repinning or template migration. |
| Configured runtime composition | Requires explicit current policy/login schemas and never auto-migrates. Connection self-management is not automatically configured. | Future runtime registration must preserve explicit template/grant/credential boundaries; directory pins do not upgrade data-plane ACLs. |

Opening identity administration needs a separate reviewed change that completes
all applicable rows, revokes/rebinds persistent grants, and implements:

- every non-no-op transition revokes prior active memberships/operators before
  assigning the new generation, including inactive→active;
- last eligible admin checks across active, suspended and archived spaces,
  excluding the target even if it would remain active under a new binding;
- preservation of at least one currently active operator when one exists, without
  inventing an operator in a valid legacy zero-operator database;
- bounded revision/epoch overflow prechecks, atomic minimal transition and scoped
  policy audits, and current-session invalidation in policy→login lock order;
- negative tests for all persistent grants and long-I/O consumers, independent
  security review and exact-head CI. Neither documentation nor a boolean flag may
  replace these gates.

## Verification limits and primary references

All tests use disposable synthetic identities/data and test-only transitions.
Migration rollback, ABA, legacy authority and bound session tests are not live
GitCode or production/power-failure acceptance. SQLite behavior is based on its
[ALTER TABLE](https://www.sqlite.org/lang_altertable.html),
[trigger](https://www.sqlite.org/lang_createtrigger.html) and
[ATTACH transaction](https://www.sqlite.org/lang_attach.html) documentation.

Return to [documentation navigation](README.md).
