# Persistent GUI observation authority

This is a saved-observation ingress, not a runner, identity provider, remote panel
adapter or production migration. The [principal transition gate](../../docs/principal-authority.md)
remains unconditionally closed in both service code and required SQL guards.

## Enrollment and current grants

`SmokeConnection` is a trusted configuration DTO, not a grant by itself. The exact
scope, complete reader/publisher lists and each recipient's policy realm, local ID,
issuer, subject and principal generation are persisted in `gui_smoke_grants`.
Every reader and publisher must match its current bound grant and existing
workspace/member/module permissions. Neither administrators nor operators have
implicit data access. HTTP input cannot configure or enroll a scope.

For a new store, explicitly call `initialize(store)` and
`initialize_connections(store, (configured, ...))`. Enrollment captures revision 1
only where both a grant and prior snapshot are absent. An existing grant with the
same DTO and current recipient pins is an exact no-op, including after restart.
A changed DTO, inactive/rebound recipient or retained legacy snapshot requires
explicit regrant; initializer retries do not recapture authority. Constructors,
HTTP requests and runtime startup never perform enrollment.

An instance's frozen DTO must match the current persisted configuration. After an
administrator changes that configuration, stale instances can no longer read or
publish under an older DTO. Missing, partial, malformed, foreign-realm and stale
identity grants fail closed, without falling back to a local ID allowlist.

## Publication and destructive regrant

`publish_snapshot(..., actor=..., expected_grant_revision=...,
expected_revision=...)` checks both current grant and snapshot revisions in the
policy write transaction. Its trusted caller must supply an **already verified**
local actor, select/sanitize the data and prove its repository scope. The actor
string is not authentication; these Python functions must not become caller-
selected HTTP operations. The actor must be a current explicit publisher with
module read/write rights. All selected readers must have current bound authority
and read permission. Publication, counter and minimal audit commit together.

`regrant_connection(..., actor=..., expected_grant_revision=...,
expected_snapshot_revision=...)` is a separate explicit, destructive current-admin
operation. It captures reviewed current recipients, advances both independent
counters, deletes saved content and records one scope-hash audit in the same
transaction. It does this even when the DTO is unchanged. It is not an automatic
retry after a failed precondition, nor permission to republish old observations.

Grant revision and snapshot revision are bounded monotonic counters; neither is
reset by deleting content. A clear produces `available=false`, a current possibly
nonzero source revision, `observed_at=null` and no tasks/counts. A later publication
advances that counter again. Readers cannot combine an old revision-pinned page
with new data after A→B→A grants or an empty interval. Both GUI entry points use
the same strict validator for this unavailable-but-versioned state.

A stale expected grant or snapshot revision rejects without changes, even if all
current DTO values again look identical. Exhausted counters cannot advance or
partially clear data; a maximum grant revision does not by itself deny an otherwise
valid existing read. Audit errors roll back the grant, snapshot and both counters.
There is no claim of protection against an administrator rewriting the database
or restoring an old backup. External anti-rollback remains a separate requirement.

## Historical publication semantics

V3 snapshots retain the realm, reader generation/issuer/subject/membership,
workspace and module revision fences. They additionally bind the exact persistent
grant digest and grant revision. Every authorized read checks its own reader fence
before interpreting task contents.

A publication is a historical sanitized observation for an explicitly approved
audience, **not continuous publisher eligibility**. A publisher later losing
membership, becoming inactive or changing identity cannot publish with its old
grant, but does not alone revoke those readers' already-authorized historical
observation. The read path does not require every historical publisher to remain
active. This differs deliberately from artifact approval's ongoing-eligibility
contract. Reader identity/membership changes still deny that reader; workspace or
module revisions still require new publication. Explicit regrant clears all old
content, including after publisher or reader ABA.

Public source/task/error DTOs expose no grant envelope, identity map, issuer,
subject or grant digest. Audit contains local actor, scope hash and revision, not
task text or private identity bindings. A trusted ingress is not a secret scrubber;
its caller must still allowlist and sanitize the selected observations.

## Legacy stores and recovery boundaries

Normal `initialize` requires either both current tables or neither table. An
original snapshot-only schema is not silently changed. Offline, explicit
`migrate_v1(store)` validates the exact old snapshot table and adds an empty grant
table under the writer lock. It preserves all snapshots and captures **no** pins.
Repeated migration validates the complete current schema before doing nothing.
A DDL or final-schema failure rolls back that schema addition.

After migration, ordinary reads/publication and first-enrollment attempts do not
adopt old observations. A current administrator can explicitly regrant a known
v1/v2 snapshot with expected grant revision 0 and its exact snapshot revision.
The new grant starts at 1, the snapshot counter advances beyond the old revision,
and the old content is deleted. A new sanitized publication is a separate action.

Recovery is not arbitrary repair: missing grant with v3 content, bound grant with
legacy content, malformed payloads, mismatched scope/counter/digest or unknown
versions reject unchanged. Structurally valid but semantically stale v3 content
may be explicitly regranted and cleared. Missing state never synthesizes a pin,
a successful task report or an approved history.

The strict configured runtime now includes the grant table in its expected schema
fingerprint. Prior installation/backup manifests are intentionally rejected; their
bytes are preserved, not rewritten to pretend migration occurred. The low-level
schema helper is not a runtime migration command, does not update those manifests
and does not authorize a production upgrade. A separately reviewed deployment
migration and protected backup/recovery plan are still required.

## Verification

[Authority regressions](tests/test_authority.py) cover principal ABA, historical
publisher versus current reader semantics, no automatic capture, two-counter CAS,
legacy/mixed-state recovery, independent-process races, exhaustion and transaction
rollback. [Core tests](tests/test_smoke.py), the shared GUI validator and both HTTPS
browser workflows retain scope, paging, malformed payload, revocation, literal
text and responsive-layout checks. Runtime tests exercise strict installation and
backup refusal without rewriting legacy evidence.

All fixtures and identities are synthetic. Passing them does not establish a real
panel's provenance, real-provider identity or production isolation/acceptance.

[Package navigation](README.md) · [Authority matrix](../../docs/principal-authority.md)
