# Local SQLite policy persistence

`SQLiteWorkspaceService` is an optional standard-library adapter for the
[workspace policy core](README.md). It preserves the core's operation signatures
and errors while persisting principals, memberships, modules, reference text
resources, policy epochs and successful-mutation audit metadata. It is **not
wired into the demo, real authentication, HTTP routes or any deployed service**.
No data is imported from an existing project.

## Synthetic example

Run from the repository root with Python 3.12+ (SQLite 3.37+ for STRICT tables):

```python
from pathlib import Path
from tempfile import TemporaryDirectory
from packages.workspaces import (
    Membership, ModuleInstance, Principal, Resource, Workspace,
    SQLiteWorkspaceService,
)

with TemporaryDirectory() as directory:
    path = Path(directory) / "synthetic.sqlite3"
    store = SQLiteWorkspaceService.initialize(
        path,
        principals=(Principal("synthetic-admin", "synthetic", "admin"),),
        workspaces=(Workspace("sample", "Sample product"),),
        memberships=(Membership("sample", "synthetic-admin", "admin"),),
        modules=(ModuleInstance("sample", "notes", ("read", "write")),),
        resources=(Resource("sample", "notes", "item", "Synthetic text"),),
    )
    store.update_resource("synthetic-admin", "sample", "notes", "item",
                          text="Updated synthetic text", expected_revision=1)
    reopened = SQLiteWorkspaceService(path)
    assert reopened.read_resource("synthetic-admin", "sample", "notes", "item").revision == 2
```

`initialize()` is explicit trusted bootstrap, not a request handler. It validates
records before allocating a new file and refuses **every existing path**, even
an empty file. Parent directories must already exist. Failure after allocation
retains the file for inspection; no automatic deletion/retry/overwrite occurs.
Normal construction uses SQLite `mode=rw`, so missing databases never silently
become empty stores. Application ID and schema version are checked on every
operation; unsupported versions fail closed, with no automatic upgrade.

## Transaction and revocation guarantees

- Each call opens its own connection. No retained Python snapshot/`Decision`
  serves as authority on later calls. The current policy and reference resources
  are loaded together and checked by the same core policy implementation.
- Writes use **`BEGIN IMMEDIATE` before loading authorization/revision state**.
  Competing processes on the same local database cannot both authorize against
  stale memberships and then commit. Revision checks, last-admin invariant,
  resource/policy changes and audit append are in one SQLite transaction.
- New workspace creation also requires the separately reviewed
  [initial-admin identity precondition](README.md#initial-admin-selection-precondition).
  Both the direct SQLite method and HTTP composition compare it only after
  obtaining the writer lock. A target changing between selection and lock
  acquisition cannot silently receive the new membership. No selection table,
  secret, expiry/consumption record or policy schema change is introduced.
- Reads use one `BEGIN` snapshot. A read that began before a revoke may finish
  against the prior snapshot. A later operation observes the committed revoke;
  prior decisions do not grant future writes. This is a transaction-order
  guarantee, not cancellation of bytes already delivered.
- Successful mutations commit together with their metadata-only audit events.
  An audit-insert failure rolls back both policy and resource changes. Failed
  calls do not append success events. No external effects occur inside a lock.
- `synchronous=FULL` and foreign keys are enabled per connection. Initialization
  uses SQLite's default rollback-journal mode; no WAL or unsafe durability mode
  is enabled. A single writer is serialized. Busy/locked transactions roll back
  and raise `StorageError("store_busy")` after the bounded timeout (default 5s,
  configurable 0–30s). Retry the **whole operation**, reauthenticating as needed
  and retaining revision preconditions; do not replay a prior permission result.
  Retain the initial-admin selection value too. The HTTP adapter alone provides
  durable creation-key replay; retrying a direct core creation has no such
  duplicate-creation protection.

`PolicyError` remains the domain authorization/revision error. `StorageError`
reports storage unavailability or unsupported/invalid stores without exposing
paths, SQL or data; it must not be converted to a successful/empty result or
confused with `not_found`. The adapter never falls back to an in-memory store.

## Trust, durability and operational limits

- Only a **trusted server adapter** may supply a current authenticated internal
  principal ID. No real login/session/provider check is added. Bootstrap
  principals and memberships remain administration inputs, not client input.
  Runtime enrollment and identity synchronization are separate work.
- The file, containing all scopes, is trusted server state. New files use private
  creation permissions where the OS supports them; this does not secure an
  untrusted parent directory, backups, other OS users or malicious in-process
  code. Use a dedicated protected local directory. Data is not encrypted.
  Application ID/version checks are not tamper-proof signatures.
- Only local filesystems with working SQLite locks are in scope. No network
  filesystems, multi-host database service, failover, backup/restore procedure,
  production upgrade/recovery procedure or rollout is included. SQLite and the filesystem
  must honor durability guarantees; tests do not simulate power loss/hardware
  failure. Runtime databases and sidecars must remain untracked and unpublished.
- This deliberately small adapter materializes all state and audit rows per call
  and upserts the reference state on mutation. It shares trusted internals with
  `service.py` to keep one policy implementation. It is not a scalable production
  query backend or a database row-level-security boundary. Future optimized
  stores must preserve the same transaction and negative-test contract.
- The audit is durable **local successful-mutation metadata only**. It is not
  immutable against a database administrator, a timestamped/request-correlated
  compliance trail, read/denial logging, an external sink or an outbox. It does
  not satisfy all audit requirements in the wider workspace contract.
- External boards, artifacts, downloads, Agent files and queues are not part of
  this transaction. Calling `authorize()` and then writing another database or
  making a network call does **not** inherit the guarantees above.

## Evidence and remaining gates

[Tests](tests/README.md) replay the policy contract against SQLite and add reopen,
atomic audit rollback, lock timeout, real separate-process lost-update and
last-admin races, a committed-revoke barrier, and abrupt-writer-exit recovery.
Fixtures are synthetic, stored in disposable temporary directories. Public CI's
workspace job discovers these tests automatically.

Real authenticated HTTP access, database-backed application resources, invitation
acceptance, shared-resource grants, provider synchronization and end-to-end
workspace isolation remain unimplemented. Do not deploy this reference adapter
or migrate existing production data on the strength of these tests alone.


## Configured request integration

The [transactional workspace API](../workspace_api/README.md) is an optional,
explicitly configured WSGI adapter. Its internal transaction callback resolves
current issuer/subject bindings from this store and invokes protected operations
in that same transaction. No callback or actor identifier comes from request
data. This does not install a provider, create a listener, modify the local demo,
or make these low-level raw-ID methods suitable as public HTTP endpoints.

## Authority schema v3 and offline compatibility

Exact core and sidecar definitions plus the closed directory/metadata guards are
checked on every operation. Each membership/operator row has exactly one
sidecar: a complete realm/issuer/subject/generation pin, or an explicit all-NULL
legacy tombstone. Missing/extra rows and partial/malformed pins are invalid
storage. Complete stale or foreign-realm pins retain their records but cannot
authorize. Unknown sidecar triggers/custom indexes are rejected. The public
membership representation remains unchanged. Persistence compares old pins
before writing, then checks the exact expected sidecars and core key sets after
all core/audit writes, rolling back unexpected mutation rather than accepting a
post-write rebind. These are integrity checks, not a sandbox against a database
administrator who can rewrite the schema or restore an entire backup.

Fresh trusted `initialize()` captures current pins. Normal loading uses a
separate persisted-data path that never captures, repins or fills missing rows.
It permits structurally valid legacy/orphaned spaces without eligible admins;
that does not make their old memberships effective. Current membership pins gate
member access, admin counts and transfer, while current operator pins gate
lifecycle/control operations and capability reporting.

Explicit `SQLiteWorkspaceService.migrate_v1(path)` and `migrate_v2(path)` are
trusted local library upgrades of the exact known source schemas to v3. Normal
construction never upgrades. The entire conversion, sidecar/tombstone creation
and final version change share one writer transaction. Existing v2 realm,
principal authority, core revisions, immutable foundation marker and unrelated
module records remain unchanged. No legacy membership/operator becomes bound.
The v1 path establishes the principal foundation and the same unbound sidecars
in one transaction. No executable deployment command is supplied here.

Ordinary edits, admin transfer and invitations cannot repair a stale/NULL pin.
Invitation re-enrollment may retain a revoked member's still-current pin, but a
changed identity requires a separately reviewed explicit recovery/regrant flow.
Neither that recovery nor identity administration is implemented. See the
[migration contract and consumer matrix](../../docs/principal-authority.md).

The v3 version is a downgrade guard: old v2 adapters must reject it rather than
ignore new sidecar tables and authorize local IDs. Configured runtime schema
manifests also change, and the library migration is not a runtime-manifest
upgrade. Restoring an entire historical database/backup can still restore old
authority; no external anti-rollback ledger or production recovery is claimed.

## Bounded-counter compatibility

Bounded-counter checks remain in force across the explicit v3 schema upgrade.
The existing core counter definitions are retained, including counters at the
maximum; no migration resets them. V1/v2 stores require the explicit policy
upgrade above even when all counters already fit the safe-integer range. Every load rejects out-of-range core revisions or
audit counters as `invalid_store`; every persistence pass validates counters
before writing policy rows. HTTP adapters report invalid storage generically.
Old oversized data is preserved but denied, never rounded, reset or silently
rewritten. Both explicit policy migrations validate the same bounds before commit, so an
oversized old record rolls back the entire DDL/data migration.

These are application/load/persistence checks, not a new SQL upper-bound CHECK
or protection against a database administrator. Existing exact-schema identity
and epoch guards stay unchanged and identity mutation remains unavailable. The
single-process preflight is atomic for counter exhaustion; SQLite also rolls
back failures after policy persistence, audit insertion or the final credential
check. Trusted compound adapters must budget all events/changed counters before
mutating their state, and retain the shared transaction for later failure paths.
