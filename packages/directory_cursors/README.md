# Directory receipt foundation (not connected to HTTP)

This package is an **unmounted foundation**, not completion of W07 or a new
permission. Existing Pipeline/Artifact directory routes and their tuple cursors
are unchanged in this slice. No client, runtime configuration, schema digest,
provider, login enrollment, refresh or execution path is added.

The only contemplated actions are `pipeline.scopes.read`,
`artifact.scopes.read`, and `artifact.scopes.admin`. The private transaction seam
has current Cookie/Bearer identity, fixed workspace/module gates and receipt
primitives. It does **not** implement the Pipeline directory projection, Artifact
per-index grants, reader publication-approver provenance, admin capabilities or
reader `NULL` candidate projection. Those projections and the two clients must
be reviewed and switched together in a subsequent increment. Do not pass an
empty/synthetic fence to a real adapter and call that W07 integration.

## Explicit factory-only storage

`initialize_directory_cursors(policy, layout="explicit-factory-v1")` explicitly
creates three auxiliary tables and one index in an already initialized policy
SQLite store. It is idempotent only for the complete identical schema and realm.
A partial/different schema, extra auxiliary trigger/index, TEMP object, invalid
record or realm mismatch fails closed. Constructors and requests never create or
repair schema. The `ReceiptStore` constructor only verifies an existing store.

Before any DDL, the initializer rejects either `installation.json` or
`.runtime.lock` in the policy file's resolved parent, including broken symlink
entries. There is no force option. This is a conservative misuse guard, not a
hostile-filesystem security boundary. Do not remove markers or move/hardlink a
runtime DB to bypass it. No real installation is modified by the tests.

**Do not run this initializer on an installed `service_runtime`.** The strict
runtime schema digest rejects these extra objects, so adding them would prevent
runtime creation, check and backup, not merely disable one directory. This slice
changes no runtime initializer/configuration/manifest/backup/restore contract.
The original empty/identity-disabled runtime remains supported by its existing
checks. Runtime receipt integration requires separately reviewed explicit
migration, lifecycle, backup/restore and rollback work.

At this foundation stage, absent `receipt_only` selects exactly the old ordinary
transaction path: directory HTTP behavior is not changed to 503 yet. In the later
atomic adapter/client migration, absent receipt configuration must close those
three directories after current authentication/scope checks, with no tuple or
first-page fallback. Existing unconfigured factories will then require explicit
migration; manual exact-scope APIs retain their original authorization. Tests of
this foundation do not prove that future adapter transition complete.

## Receipt and partition semantics

- `dcr1_` plus 32 CSPRNG bytes, canonical base64url without padding: 48 ASCII
  characters. Receipt existence and unpredictability prove server issuance.
  SHA256 digests only compress internal equality inputs; they are not MACs.
- Current principal pin (realm/local ID/issuer/subject/generation), route
  workspace, fixed action, membership role/state/revision/binding, workspace and
  module revisions, page size 25, empty filters, protocol/order versions and
  admin `allow_refresh` form the common context. No global policy epoch.
- The future action-specific trusted `Projection` supplies distinct authority
  and visibility digests. Its first result is cached for the callback; one
  post-DML projection verifies it. At most two projection calls, not one per
  stored receipt. Its `value` is a private working view, not stored in receipts.
- The last visible `(connection_id, object_id)` is ASCII, at most 128 bytes each.
  No hidden scan offset or total is stored/returned by this foundation.
- TTL is exactly 300 seconds. Reusing the same context/fences/position reuses the
  receipt without extending expiry. Replay is allowed only with current identity
  and authorization. The token is principal-bound, not session- or tab-bound.
- At most 32 receipts per `(local actor_id, workspace, action)`, read with a
  33-row sentinel. Only that partition's expired rows may be cleaned; unexpired
  entries are never evicted. A full partition fails without exposing counts.
  Different local actor IDs/workspaces/actions do not share the quota.
- The same local actor ID rebound to another identity generation **does** inherit
  its previous quota and safe clock floor. Old tokens still fail the full pin
  comparison. This is not identity-generation resource isolation. Actual runtime
  identity transition remains closed; its future migration must handle this
  availability/time inheritance explicitly, never by silently deleting floors.
- Total storage scales as 32 times configured principal/workspace/action
  partitions, not a constant global cap. Clock floors remain when receipts expire.
  There is no global LRU, hidden-account eviction, background worker or network
  rate-limiting claim.

All receipt fields use bounded BLOB projections before strict decoding, including
NUL, UTF-8 and one-byte-overflow rejection. The original policy loader still reads
global policy/resources/audit. This does not make the entire request bounded or
remove disk/lock/CPU contention and existing corruption side channels.

## Private receipt-only transaction contract

Only a trusted caller can pass `receipt_only=ReceiptOnly(...)` to the existing
private authenticated seam. No HTTP field selects it. It requires
`write=False`, `with_connection=True`, and **no** `before_commit` hook. A receipt
callback takes `(policy, actor, db, receipts)` and must explicitly `arm()`; arm
rechecks the fixed action's gate before schema/partition reads. The W05 conflict
callback never arms. A successful unarmed callback is rejected.

Both authentication modes take `BEGIN IMMEDIATE` before policy reads. Cookie
mode still reserves both local DELETE-journal databases and uses the original
session/realm/generation/current-principal verification. GET does not acquire a
CSRF requirement; ordinary mutations still require it. Bearer protocol verification
may perform identity-provider I/O before locking; receipt work does not perform
business-provider I/O. The normal paths retain their old behavior.

Receipt-only has one independent finalizer, not the old Cookie `business`
savepoint exception handler. Its `receipt_work` savepoint includes partition
creation, issuance and cleanup. The controller captures the original partition
and its complete bounded receipt set (R0). On callback/guard error it rolls back
and verifies exact R0, then performs final current authentication even if the
callback threw. Only safe clocks survive; original errors are not swallowed.

Raw callback SQL is read-only under a phase-specific SQLite authorizer. Only
controlled store phases allow exact auxiliary-table DML; callback writes to
resources/grants/audit/login sessions, transaction control, PRAGMA writes and
TEMP/schema DDL are rejected. The authorizer is reset at each phase, including
prepared-statement cache boundaries. This is defense against accidental trusted
SQL, not a sandbox against hostile Python that replaces the authorizer or mutates
private objects. All receipt and schema readbacks/CAS checks still apply.

After final authentication, the sole permitted DML exception is exact partition
floor and attached login clock CAS, with schema/no-trigger checks, bounded
receipt-set and actor/session projections before/after. No new clock observation,
projection scan, callback or network call follows. There is no current→clock
UPDATE→current loop. Receipt TTL is judged at its last receipt guard; this does
not promise validity at arbitrary commit/network arrival time.

Ordinary errors must commit the highest legal observed clocks before propagating.
Expiry rejection followed by a wall-clock rewind cannot revive a receipt after
that clock-only commit. Invalid/rewound clocks are errors, not clamped successes.
Final auth failure replaces a cursor diagnostic, rolls back new receipts/cleanup,
and retains only armed safe time. Unarmed authentication/scope errors cannot
create receipt floors; Cookie's existing login clock safety still applies.

A failed rollback/schema/CAS/commit that prevents safe finalization produces no
success/token. **Uncommitted observations cannot be guaranteed durable during
storage failure.** A commit may have succeeded even when its result is unknown;
no success is inferred and the whole request is not automatically replayed.
These faults require trusted storage/time diagnosis before resumption; there is
no implemented multi-process fail-stop latch or automatic recovery protocol.
Do not equate normal process-restart tests with storage-fault recovery. Whole DB
rollback, hostile local writers, forced process death/power loss are not solved.

## Evidence and implementation boundaries

Tests are placed in the existing CI discovery roots, not an undiscovered new
suite. They use temporary SQLite, synthetic identities and `spawn` processes:

- [Store tests](../workspaces/tests/test_directory_cursors.py): explicit and
  idempotent schema, runtime-marker guard, partial schema, bounded NUL/UTF8 data,
  TTL/reuse, quota isolation, real cross-process restart and 31→32 competition.
- [Bearer tests](../workspace_api/tests/test_directory_cursors.py): two projection
  passes, current identity/error precedence, W05, raw SQL write rejection, ABA,
  real SQLite COMMIT BUSY and injected before/after-commit errors, writer wait,
  error-path clock retention and final identity readback.
- [Cookie tests](../session_api/tests/test_directory_cursors.py): real stored
  session/cookie shape, base→session delegation, CSRF unchanged, final expiry,
  rollback, both clocks, TEMP trigger rejection, process restart and logout.

The injected commit faults are not a real I/O failure proof. The COMMIT BUSY
case uses a real competing SQLite reader and checks the actual rolled-back state.
No tests/build/browser were run locally while preparing this increment; execution
belongs to the existing remote CI. Passing that CI would establish only this
foundation, not three migrated HTTP directories, real providers or production.
