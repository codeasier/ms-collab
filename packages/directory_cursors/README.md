# Explicit-factory directory receipts

Three configured directory actions use this auxiliary store: `pipeline.scopes.read`,
`artifact.scopes.read`, and `artifact.scopes.admin`. This is not a new permission,
completion of every W07 pagination surface, runtime integration, real-provider
acceptance or production migration. Exact manual-ID APIs retain their existing
current authorization. Other pagination protocols are unchanged.

The Pipeline and indexed Artifact adapters and both cookie scope pickers switch
atomically to opaque receipts. Old tuple cursors have **no fallback**. The
standalone UIs remain manual. No client token goes in a URL used for navigation,
browser storage, bootstrap or cross-tab messages; it is only a GET API parameter.

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

### Factory support and compatibility break

`directory_cursors=None` is default closed. Trusted setup explicitly initializes
once, then passes `ReceiptStore(policy_store)` to `ApplicationHost`,
`PipelineApplication`, `IndexedArtifactApplication` and, for standalone use,
`ArtifactAdministrationAPI`. The host passes that same handle only to the three
fixed directory adapters. A custom trusted clock can be supplied to the store.
Constructors do not infer opt-in from existing tables or create schema.

| Factory / state | Directory result after current auth/scope/module gates | Other routes |
| --- | --- | --- |
| No handle, schema absent | 503 `scope_directory_unavailable`, no DDL | Exact APIs unchanged |
| No handle, complete schema | Same 503; schema does not imply consent to enable | Exact APIs unchanged |
| Handle with missing/partial/wrong-realm schema | Constructor or request fails closed; no repair | No claim that a corrupt DB can serve |
| Explicit same-DB handle, complete schema | Opaque protocol for mounted actions | Existing exact authorization |
| Artifact admin not mounted | 404, never auto-enabled | Original host switches |
| Original `service_runtime` | Identity stays disabled; no handle or receipt schema | Original check/backup/restore |
| Runtime with accidentally added receipt objects | Strict runtime schema digest rejects entire layout | No automatic repair or digest change |

**Existing external configured factories that relied on tuple directories must
explicitly migrate. Without a handle those formerly available directories now
return 503, even if their schema already exists.** Manual entry remains usable.
Unknown/nonmember workspace is still 404, invalid session/identity 401, denied
module capability 403, unavailable lifecycle 409. These precede token diagnostics.

## Receipt and partition semantics

- `dcr1_` plus 32 CSPRNG bytes, canonical base64url without padding: 48 ASCII
  characters. Receipt existence and unpredictability prove server issuance.
  SHA256 digests only compress internal equality inputs; they are not MACs.
- Current principal pin (realm/local ID/issuer/subject/generation), route
  workspace, fixed action, membership role/state/revision/binding, workspace and
  module revisions, page size 25, empty filters, protocol/order versions and
  admin `allow_refresh` form the common context. No global policy epoch.
- The action-specific trusted `Projection` supplies distinct authority
  and visibility digests. Its first result is cached for the callback; one
  post-DML projection verifies it. At most two projection calls, not one per
  stored receipt. Its `value` is a private working view, not stored in receipts.
- The last visible `(connection_id, object_id)` is ASCII, at most 128 bytes each.
  No hidden scan offset or total is stored/returned.
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
NUL, UTF-8 and one-byte-overflow rejection. The receipt query explicitly uses the
existing position index's partition prefix and suffix order, fetches at most 33
rows, then sorts only those bounded rows by token in memory. `LIMIT` alone would
not bound a partition-wide sort; CI asserts the actual SQLite query plan and a
VM-instruction ceiling for 33-row and larger over-quota corruption fixtures. The original policy loader still reads
global policy/resources/audit. This does not make the entire request bounded or
remove disk/lock/CPU contention and existing corruption side channels.

## Action-specific authority versus data

The common context is scoped to current identity, membership, workspace and the
target module. It never substitutes the global policy epoch for authority.
Pipeline adds no per-snapshot grants: ordinary snapshot revision/content changes
and added keys do not invalidate receipts. A missing anchor does. This is current
keyset pagination, not a stable multi-request snapshot; insertions before an
anchor require explicit reload to be seen.

Artifact reader uses only **visible published scopes**: bound policy digest and
grant revision, the actor's existing stored grant binding, actual current
approver principal pin and membership role/state/revision/binding, and stored
publication authority/membership/policy proof. Full snapshot digest remains
validated by the codec but is not an authorization epoch. Candidate, private
index revision, public content revision and snapshot digest are excluded from
the fence. The reader still selects SQL `NULL` for candidate; denied payload
bytes are not decoded early. Valid empty publication is visible; stale/no proof
is not.

Artifact admin uses the exact status evaluator's effective capabilities, bound
policy/grant and actor binding, with actual host `allow_refresh` in context.
It does not inherit the reader's publication-provenance fence or include pending,
candidate/index/publication revisions. Nonreader approvers and write-only
refresher behavior remain unchanged, including approve-requires-review.

Both Artifact actions separately fingerprint the **visible scope key set**.
Visible additions/removals require explicit reload; this visibility fence is not
called an authorization epoch. Same-authority content updates continue. Normal
changes to still-hidden scopes do not add an invalidation signal; the prior
hidden-record corruption/capacity side channel remains. Host allow_refresh values
are compared, but an unobserved false→true→false configuration cycle has no new
persistent host epoch and is not claimed as one.

Each successful directory does at most two full bounded projections, one before
paging and one before final identity validation, each with its original limits.
It does not scan once per stored receipt. This does not bound the existing global
policy loader or physical SQLite lock/storage latency.

### Wire and client behavior

`next_after` is null or canonical `dcr1_` plus 43 base64url characters (48 total).
Only optional `after` is accepted. Old tuples, malformed/duplicate/empty values,
unknown/expired tokens and cross-principal/workspace/action or changed-fence
receipts all return value-free `409 scope_directory_changed`. Unknown filter
names return 400 after authorization. No error echoes a token, count or digest.
Storage and codec failures remain value-free 503, never empty successful lists.

Clients retain the last visible tuple **only in local memory** to validate strict
cross-page ordering; it is not sent as authority or as the server cursor. They
validate canonical opaque format, full-page continuation, no repeated token,
item schemas and scope. A same-loaded-directory selection/Open clears old exact
results but preserves that directory's position. Workspace/manual edits/reload,
page fetch, logout/pagehide/Back and controller clear reset token and tuple
together. Next captures its pair before clearing. 409/503 clears selection and
pagination, offers explicit reload and never retries, falls back or autoloads.
Existing independent generations/ignored abort and double-tab isolation remain.

## Closed-directory rejection and final authentication

Unconfigured directories use a private fixed-action denial plan in the same
transaction controller, not an ordinary read callback. It has no storage handle
or projection and never constructs/arms a receipt context or performs dedicated
receipt schema/row queries. The original global policy loader and its whole-DB
foreign-key check remain unchanged; this is not a claim about SQLite internal
page reads. Current authentication runs after writer reservation; both real gate
rejections and the outer W05 replacement callback pass through the sole final
current-authentication path. With still-valid credentials, the original
400/403/404/409 gate error (or missing-configuration 503) is preserved. Final
identity expiry/unavailability instead yields 401/503. Cookie's highest safely
observed login clock is committed without a receipt floor or business DML.
Initial authentication failure does not invent an actor for a final check.
Ordinary read/write error behavior and mutation CSRF are unchanged.

## Private receipt-only transaction contract

Only a trusted caller can pass `receipt_only=ReceiptOnly(...)` to the existing
private authenticated seam. No HTTP field selects it. It requires
`write=False`, `with_connection=True`, and **no** `before_commit` hook. A configured
receipt callback takes `(policy, actor, db, receipts)` and must explicitly `arm()`; arm
rechecks the fixed action's gate before schema/partition reads. The W05 conflict
callback never arms. A successful unarmed callback is rejected; the private
unavailable-directory plan always rejects and cannot issue a receipt.

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
resources/grants/audit/login sessions, transaction control and TEMP/schema DDL
are rejected. PRAGMA admission is an explicit read-only name/schema allowlist:
main application_id/user_version/foreign_key_check/database_list/journal_mode/
synchronous, and login application_id/user_version/journal_mode/synchronous.
Arguments, TEMP/other schemas and trigger/view origins are rejected. No-argument
incremental_vacuum, optimize and wal_checkpoint are denied too; no argument does
not imply no side effects. The authorizer is reset at each phase, including
prepared-statement cache boundaries. This is defense against accidental trusted
SQL, not a sandbox against hostile Python that replaces the authorizer or mutates
private objects. All receipt and schema readbacks/CAS checks still apply.

After final authentication, the sole permitted DML exception is exact partition
floor and attached login clock CAS, with schema/no-trigger checks, bounded
receipt-set and actor/session projections before/after. Cookie principal readback
uses the already authenticated actor's exact primary-key lookup, not a CAST
predicate scanning all principals; initial authentication failure does not invent
an actor from stored session bytes. No new clock observation,
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

Integration tests live in the same existing discovery roots:
[factory/runtime matrix](../application_host/tests/test_directory_cursors.py),
[closed-directory final-auth matrix](../application_host/tests/test_directory_closed.py),
[standalone adapters and spawned workers](../workspace_api/tests/test_directory_integration.py),
and [Cookie action/authority matrix](../application_ui/tests/test_directory_receipts.py).
The existing Pipeline/Artifact projection suites retain codec, NUL/UTF8, actual
scan/byte bounds and candidate exclusion checks. Existing HTTPS journeys add
context/action token replay and keep their old assertions/screenshots/cleanup.
No workflow, runner, timeout or permission is changed.

The query-plan checks use `EXPLAIN QUERY PLAN` on the actual production query
strings, not a string assertion that `LIMIT` exists. Their expectations concern
index searches/no temporary sort, not unstable full explain text. The extra
failure tests cover restore/rollback failure, final-auth BaseException, login
clock CAS failure and an interrupt during clock cleanup. Failure-state injection
is labeled separately from real SQLite CAS and lock behavior.

The injected commit faults are not a real I/O failure proof. The COMMIT BUSY
case uses a real competing SQLite reader and checks the actual rolled-back state.
No tests/build/browser were run locally while preparing this increment; execution
belongs to the existing remote CI. Passing that CI would establish only this
explicit-factory three-directory behavior, not every W07 surface, runtime
integration, real providers or production.

### SQLite references for these boundaries

SQLite documents how [query plans distinguish searches and temporary sorting](https://www.sqlite.org/eqp.html)
and that [incremental_vacuum can reclaim pages without an argument](https://www.sqlite.org/pragma.html#pragma_incremental_vacuum).
The [authorizer action contract](https://www.sqlite.org/c3ref/c_alter_table.html)
provides the pragma name, optional argument and database scope; the foundation
checks that complete tuple instead of treating every argument-free pragma as a read.
