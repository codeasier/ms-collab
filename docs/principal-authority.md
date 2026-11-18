# Principal authority and durable workspace grants (closed identity administration)

This increment establishes persistent authority coordinates, durable membership/operator
grant ownership and conservative consumer invalidation. It **does not enable identity provisioning, rebinding,
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

## Workspace membership and operator ownership

Policy schema **v3** retains the public `Membership` shape and existing core rows,
but adds a dedicated authority sidecar for every membership and operator record.
Each bound sidecar records the policy realm and the principal's exact issuer,
subject and generation; its primary/foreign key supplies the local principal ID
and, for memberships, the workspace ID. A local ID, active flag, role or matching
membership revision alone never grants authority.

- Only explicit trusted fresh bootstrap or creation of a genuinely new membership
  captures current authority. Normal constructors, persisted loading, unrelated
  writes and reads never capture or repair an old grant.
- Explicit offline policy upgrades retain every legacy membership/operator row
  and create an **all-NULL authority tombstone** for it. This is recognized but
  unbound data, not an effective grant. Missing/extra sidecar keys, partial NULLs
  or malformed pins are invalid storage, not an invitation to backfill. Unknown
  triggers/custom indexes on the sidecar tables are unsupported schema.
- A complete but old-generation, changed-identity or foreign-realm pin is stale.
  It remains stored and denies access. Returning to the old issuer/subject after
  A→B→A, or deactivation/reactivation, cannot make the old generation current.
- Member access, workspace discovery, eligible-admin counts, operator checks and
  capability reporting use the current durable grant. A stale active admin does
  not protect another admin's demotion; a stale operator is not an eligible
  operator. Public model responses do not expose these private authority pins.
- Ordinary member edits retain the existing pin. A stale active membership may
  be revoked, but cannot be promoted or repaired by an edit; admin transfer
  requires a currently bound replacement. Invitation acceptance creates a pin
  only for a member with no prior row. Re-enrollment of a revoked member requires
  its existing pin to remain current and retains that same pin. Invitations do
  not adopt legacy NULL or stale grants into a newer identity.

Fresh bootstrap still requires an eligible admin for every supplied workspace.
Strict persisted loading instead validates structure without inventing authority:
a retained legacy/orphaned workspace may have no eligible admins and remain
loadable, but its unbound/stale members cannot authorize access. This distinction
avoids making one orphaned workspace corrupt every unrelated scope. Existing
valid zero-operator installations remain valid; no operator is invented.

Recovery/regrant of these unbound or stale core grants is a separate unfinished
administrative workflow. This slice adds no online recovery, identity transition
or privilege-rebinding entry point. Historical audit `actor_id` values are also
not retroactively pinned or relabeled as the current identity's actions.

Schema v3 is intentional: a v2 adapter must reject its `user_version`, rather
than silently ignore additive sidecars and revert to local-ID authorization.
This version check is not backup anti-rollback protection.

## Initial-admin selection before new workspace creation

The operator's [creation workflow](../packages/workspace_admin_api/README.md#initial-admin-identity-selection)
now separates a single-target identity review from confirmation of a new grant.
`GET /api/control/initial-admin-selections/{principal_id}` returns only the exact
selected local ID and an `ias1-` SHA-256 precondition. It binds version, purpose,
realm and both actor/target local ID, issuer, subject and generation. A new
creation must supply `If-Initial-Admin-Match`; the core and SQLite public methods
also require the expected value and compare under the mutation lock.

This is an equality precondition, not a secret, unforgeable proposal,
authentication capability or external identity verification. There is no TTL,
single-use consumption, identity reservation or new storage schema. An unchanged
value can survive normal restart. It does not establish that the person chosen
before the GET was correct, and restoring a full historical database with the
same pins is still outside the anti-rollback guarantee.

The original three-field create body, v2 retry namespace/digest and persistent
actor/target provenance are retained. Current authority is checked first; a
valid existing-key exact replay returns without a new selection requirement or
new grant. Only an unused key must pass the selection check. Old clients can
therefore recover a still-current old result but cannot create a new space
without the new header. Stale pins/tombstones/conflicting bodies never become
fresh requests. The UI requires explicit Review and Create actions and preserves
unresolved key/precondition pairs; only a confirmed no-write 412 permits a later
explicit review to update the precondition with the same key.

This closes only the new workspace initial-admin selection-to-write race.
Invitations have their own [recipient-review contract](../packages/workspace_invitations/README.md#explicit-recipient-identity-review):
an exact current-admin GET followed by a purpose/workspace/role/actor/target-bound
equality check on an unused request key in the writer transaction. Current-authority
historical invitation replay still returns its original result, including
accepted/invalidated history, without interpreting the new header or re-granting
membership. Other recipient-selection workflows, durable grant recovery,
transition-wide revocation, last-eligible-operator safeguards and historical audit
ownership remain separate gates. Both identity mutation entry points and real
GitCode login remain hard closed. The
[selection acceptance plan](../packages/workspace_admin_api/tests/README.md#initial-admin-selection-acceptance-plan)
requires exact-head CI and is not itself an execution result.

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

## Explicit policy v1/v2 → v3 upgrades (offline, operator-controlled)

Normal policy constructors require v3. They never auto-upgrade or create a
missing file. The trusted local helpers `SQLiteWorkspaceService.migrate_v1(path)`
and `SQLiteWorkspaceService.migrate_v2(path)` convert only their exact known
source schemas to v3. Login storage remains separately versioned: its explicit
`SQLiteLoginStore.migrate_v1(path, binding=..., clock=...)` upgrades login v1 to
v2. These are library operations, **not HTTP routes, runtime auto-migrations or
a deployment command**. Their existence does not authorize a production upgrade.

1. Stop request-serving processes. Establish an authorized, protected backup and
   a rollback plan; login stores include sensitive short-lived verifier material.
   Do not put database files, sessions, verifiers or raw dumps in source control.
2. Under `BEGIN IMMEDIATE`, validate application ID, exact known core definitions,
   indexes/triggers and retained data. Policy module tables are retained; modified
   core tables or unexpected core triggers/indexes are rejected. Login has no
   extension tables requiring migration.
3. Execute migration DDL and row changes individually in the same transaction.
   A v1 policy receives its new realm, generation/authority epoch and closed
   principal guards; an existing v2 policy retains those values and its immutable
   `authority_migrations` foundation marker. That marker describes the principal
   foundation v2, not the later v3 workspace-grant schema. Both policy paths add
   sidecars with explicit NULL tombstones, without binding any legacy grant.
   Validate the complete target state before committing its schema version.
   Fault-injection acceptance must prove every failure leaves source schema,
   records, triggers and version unchanged.
4. Reopen only with the current policy/login versions. Policy upgrades preserve
   role/state/revision history and unrelated module records, but do not restore
   effective membership or operator access. Core grant recovery requires a
   separately reviewed explicit workflow; an old `active` row is insufficient.
   Login v1 upgrades leave old session/attempt authority NULL and require a new
   verified login. History fences without generation are hidden; legacy GUI
   snapshots require explicit grant migration/regrant before a new publication;
   legacy pending invitation digests are invalidated. Do not silently rewrite
   retained records or export them as a recovery shortcut.

Configured runtime installation manifests also pin the exact schema. A policy
library upgrade does not update an existing runtime manifest or supply a runtime
upgrade/recovery procedure. Old runtime layouts remain unavailable until that
separate operational gate is met.

The principal foundation marker records source/target version, principal count
and generated realm, with no credential, external identifier or operator claim.
It is not an audit of identity transitions or membership regrants. Legacy
inactive principals and their old membership/operator rows remain retained but
unbound, and no supported path reactivates or adopts them. A future identity
transition must explicitly revoke prior active grants. Restoring a historical
policy backup can restore old authority state; this slice does not provide an
external anti-rollback ledger, tamper evidence or production recovery acceptance.

## Consumer inventory and activation gates

This inventory is scoped to the implemented modules, not proof that every future
consumer is safe. A cookie UI uses the session fence, but that **does not upgrade
its underlying persisted local-ID ACLs**. New module integrations must be added
here before identity administration can open.

| Consumer | This increment | Remaining gate before executable identity transitions |
| --- | --- | --- |
| Login attempts and sessions; session-backed host/UI | Joint realm/epoch issuance and exact current ID/generation verification; legacy NULL denied in composed host. | Real provider bound-code acceptance remains separate. Backup rollback is not solved. |
| Saved assistant history and blocked ACP preflight | Persistent realm/generation fence; old records hidden, no automatic recovery; preflight reuses exact history fence. | Object-level lineage, executable runner isolation/authorization and recovery not implemented. |
| GUI observation snapshots and publisher configuration | [Persistent grants](../packages/gui_smoke/GRANTS.md) bind every reader/publisher to realm/generation; publication has grant + nonreset snapshot CAS. Explicit regrant clears old content, stale configurations cannot publish, and legacy rows are never auto-adopted. | Historical publisher eligibility does not retroactively rescind an authorized reader observation; live panel provenance, protected deployment migration and actual identity administration remain separate. |
| Workspace invitations | Inviter and invitee digests include realm, ID, issuer, subject and generation; old digests never upgraded. Explicit single-recipient Review binds current actor/target pins, workspace, role and purpose before an unused-key write; exact historical replay is unchanged. | Actual directory transitions/recovery and other identity-safe administrative workflows remain closed. Review is equality, not a credential or reservation. |
| Artifact byte grants and streams | Persistent realm/generation grants, independent monotonic byte revision and reviewed-parent index pin; full flow pin checked through acquisition/output. [Contract](../packages/artifact_sync/GRANTS.md). | Real cloud acceptance and host resource recovery remain separate; regrant never recycles uncertain active leases. |
| Community reads/review/refresh/approve | [Bound persistent grants](../packages/community_api/GRANTS.md), retired-ID pins, monotonic grant revisions, approver generation/membership provenance and per-page actor/config guards. Known legacy grants require explicit CAS regrant that discards old content. | Management/template constraints remain additional gates, not substitutes for data-plane grants. Standalone provider calls do not claim workspace authorization; already dispatched I/O cannot be undone. |
| Artifact index/read/review/approve/refresh | Persistent role grants and approver/membership provenance; explicit destructive CAS regrant; admission-origin flow pin and before/after-page checks on both bearer and cookie paths. [Contract](../packages/artifact_sync/GRANTS.md). | Legacy payloads stay unavailable until explicit regrant; no actual identity mutation or live-provider acceptance is enabled. |
| Pipeline snapshots and acquisition | Reads use current workspace/module policy. [Bearer acquisition](../packages/pipeline_sync/README.md) pins realm/local actor/issuer/subject/generation and scoped workspace/membership/module revisions across each GET and final publication; no automatic recapture within a flow. | New requests also require current durable workspace membership grants; transition-wide revocation before identity administration remains unimplemented. Dispatch is not atomic with local checks; remote pagination/live compatibility and whole-policy rollback remain outside this slice. |
| Research assets | Acquisition pins realm, ID, issuer/subject, generation and current membership/workspace/read-module/sync-module revisions before and after each source, then again under the publication writer lock. Reads keep current workspace/module policy and fixed source binding. | The trusted prepare call is one boundary, not proof of authority checks for every provider-internal request. Real adapters, identity-safe membership transitions and backup anti-rollback remain separate gates. |
| Administrative creation/installation retries and initial-admin selection | [Identity-owned v2 records](../packages/workspace_admin_api/README.md#durable-retries-and-transaction-boundary) retain the old key namespace/digest; actor/initial-admin pins gate replay. New workspace creation additionally compares an explicitly reviewed actor/target identity value under the writer lock; old exact replay creates no grant and remains compatible without the header. | Invitation selection is separately scoped above, with different historical retry semantics. Other-recipient selection, runtime upgrade recovery and incarnation-aware authentication remain separate gates. The precondition is neither a credential nor a timed/consumable proposal. Historical audit actor IDs are not upgraded. |
| Memberships and operators | V3 persistent sidecars bind realm/local ID/issuer/subject/generation; legacy upgrades leave explicit NULL tombstones; ordinary edits, transfer and invitations never repin stale grants. Eligibility checks exclude stale admins/operators. | Explicit recovery/regrant, executable transition-wide revocation and last-eligible-operator transition safeguards remain unfinished. Valid legacy zero-operator databases must not gain an invented operator. Historical audit actors remain unpinned. |
| Responsibility grants, owner/backup references and path evidence | [Bound grants and reference history](../packages/responsibility_api/REFERENCES.md); ordinary edits retain retired pins; explicit current-editor proposal/CAS reconfirmation; publisher provenance gates retained evidence. Legacy requires an explicit destructive current-admin grant reset; mixed formats deny. | Ownership remains observation, never permission/assignment. Trusted actor inputs require separate host authentication; real recovery, identity transitions and whole-policy rollback remain outside this slice. |
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

The v3 acceptance plan is recorded in the [workspace tests contract](../packages/workspaces/tests/README.md).
Documentation is not a test result: this change requires exact-head existing CI,
including the affected API/session/runtime consumers, before acceptance. No local
UT/build run or production verification is claimed for this slice.

All tests use disposable synthetic identities/data and test-only transitions.
Migration rollback, ABA, legacy authority and bound session tests are not live
GitCode or production/power-failure acceptance. SQLite behavior is based on its
[ALTER TABLE](https://www.sqlite.org/lang_altertable.html),
[trigger](https://www.sqlite.org/lang_createtrigger.html) and
[ATTACH transaction](https://www.sqlite.org/lang_attach.html) documentation.

Return to [documentation navigation](README.md).
