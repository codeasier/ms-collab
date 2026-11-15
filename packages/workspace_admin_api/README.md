# Workspace lifecycle and module administration

A configured HTTP extension of the [transactional workspace API](../workspace_api/README.md),
used by the [management UI](../workspace_ui/README.md). This is not a default
identity, login flow, deployment, global directory, connector provisioner or
production migration. Test verifiers exist only in [tests](tests/README.md).

## Trusted composition and setup

```python
from packages.workspace_admin_api import (
    ModuleTemplate, WorkspaceAdministrationApplication, initialize_administration,
)

# Explicit one-time setup on the same reviewed policy store, not an HTTP action.
initialize_administration(configured_policy_store)
administration = WorkspaceAdministrationApplication(
    api=configured_workspace_api,
    module_catalog=(ModuleTemplate("artifacts", ("read",)),),
)
```

The existing API supplies the verified identity and current SQLite policy.
`initialize_administration` creates the exact v2 retry-record table in that same
store. It does not discover, replace, migrate or connect any existing deployment.
Without explicit initialization, creation/installation fail closed. All existing
API routes are delegated unchanged. The extension is a `WorkspaceApplication`
subclass and can be passed explicitly to `WorkspaceManagementUI`.

The module catalog is immutable, trusted application-factory configuration.
There is no HTTP endpoint to add platform operators, change the catalog, assign
identities or supply capabilities. An empty catalog approves no new modules.
Templates describe **local authorization capabilities**, not software packages,
secrets, provider access, executable jobs or an enabled user-facing product.

## Authority and HTTP contract

All success responses retain the existing `{"data": ...}` envelope and HTTP 200.
Errors are safe codes with no provider, directory, SQL or credential details.
The existing bearer, body-size/type and duplicate-key checks continue to apply.

| Method | Path | Authority / exact body |
| --- | --- | --- |
| GET | `/api/control/capabilities` | Current principal; only `manage_workspaces` boolean |
| GET | `/api/control/workspaces` | Platform operator; ID/name/kind/state/revision only |
| POST | `/api/control/workspaces` | Platform operator; `name`, `kind`, `initial_admin` |
| PATCH | `/api/control/workspaces/{id}` | Platform operator; `state`, `If-Match` |
| GET | `/api/workspaces/{id}/modules` | Current workspace admin; module metadata |
| GET | `/api/workspaces/{id}/module-catalog` | Active workspace admin; approved templates |
| POST | `/api/workspaces/{id}/modules` | Active workspace admin; `key` |
| PATCH | `/api/workspaces/{id}/modules/{key}` | Active workspace admin; `state`, `If-Match` |

Creation atomically allocates a server UUID, a product/shared workspace and one
explicit active initial admin from the already verified local directory. There
is no global directory search or email/display-name identity matching. A platform
operator is **not** enrolled unless explicitly nominated for the new workspace,
and receives no member/module/business-data access to existing workspaces.
The minimal platform lifecycle list includes suspended spaces for recovery; the
ordinary `/api/workspaces` list remains membership-scoped and excludes suspension.

Workspace transitions preserve the existing policy: active to suspended or
archived; suspended to active or archived; no transition out of archived and no
delete. Archival retains authorized local reads and membership reductions, but
rejects new module installation or changes. A workspace admin cannot perform
platform lifecycle operations merely because they administer that workspace.

Module installation uses only the server's current template and starts
`disabled`. It preserves immutable workspace ownership and cannot overwrite an
existing module or expand its capabilities. State changes follow
`disabled -> enabled -> disabling -> disabled`; every change needs the module's
own revision. Re-enabling additionally requires the current approved template
to match the installed capability set. A removed or changed template cannot
silently reactivate an old configuration. Disabling remains possible without
catalog approval. Removing instances or migrating their capabilities is deferred.

This slice denies subsequent local module access as soon as disabling begins.
It does **not** cancel external jobs, revoke upstream tokens, provision connectors
or erase cached/copied data. Do not imply those effects from the lifecycle label.

## Durable retries and transaction boundary

Both POST endpoints require a canonical UUIDv4 `Idempotency-Key`. The key's
namespace remains **local actor ID + action + scope + key**, not generation or
realm. A previously used key never becomes a fresh mutation just because identity
changed. No pruning, automatic key rotation, reset or regrant endpoint exists.

New records retain strict realm, ID, issuer, subject and generation provenance
for the executing actor. Creation additionally captures the nominated initial
admin at execution time, deduplicated if it is the actor. Pins are stored as
bounded canonical JSON, not a credential or a request body. This identifies who
was actually nominated then. It does **not** prevent a selected local ID changing
between a human choosing it and the request acquiring its lock. A server-issued
identity-selection proposal/precondition remains required before identity
transitions can be enabled; this retry slice is not that protocol.

Current authentication and operator/admin authority are checked before looking
up a key, in the same SQLite transaction. An identical body with all original
identity pins still current returns the current referenced metadata without
another mutation or audit. Module replay does not reset its state. A changed
body, stale actor or initial-admin binding, foreign realm, or known legacy
unbound record returns generic `409 idempotency_conflict`, without the old result
or identity. Malformed bound provenance/digests/results fail safely with 503.
A missing result never turns into a cache miss and a second creation.

Creation/installation, provenance, retry metadata and audit commit or roll back
together. Assertion expiry after mutation but before commit rolls all changes
back. The existing final session/expiry guards and role/lifecycle rules remain;
these records do not upgrade memberships/operators or historical audit actor IDs.
Old bearer credentials are not made incarnation-aware by this mechanism.
Identity mutation remains hard closed; it requires those separate gates.

Retain a key across uncertain responses. The UI retains creation keys only while
the connected page remains open, has no automatic retries and preserves a 409
rather than inventing a new key. Inspect currently authorized control/module
metadata before deliberately starting a new request after a conflict or page
reload. An aborted request does not undo an already committed mutation. A new
UUID means a consciously new request, not recovery of the old one.

### Explicit offline v1 conversion

Normal requests and `initialize_administration` reject v1, even if it is empty,
v1/v2 coexistence, unknown versions and modified table/index/trigger definitions.
Every retry checks this schema again; startup validation is not sufficient.
Fresh initialization never silently adopts a deployment's old data.

The trusted library helper `migrate_administration_v1(store)` is an **offline
format conversion**, not an HTTP route, runtime upgrade command or authority
recovery. Stop all serving processes, obtain an authorized protected backup and
plan recovery before invoking it on an approved store. It requires the current
core policy schema and exact prior retry schema under one writer transaction.
All old rows must pass structural and resource-reference validation. Rows from
inactive/revoked actors, lost operator roles, suspended/archived workspaces and
disabled modules are retained, not filtered by current permissions.

The helper copies every original actor/action/scope/key/digest/result verbatim to
v2 with **NULL authority for every legacy row**, then removes only the old table.
It never captures today's identity as yesterday's owner. These keys are permanent
409 conflict tombstones, including if the formerly valid result later disappears;
normal requests never dereference a tombstone's old result. A malformed schema,
dangling legacy reference or any DDL/copy/final-validation failure rolls the whole
conversion back. Business data and prior audits are retained; no business event
or current identity attribution is fabricated. The result is only the count of
preserved tombstones. Repeating the v1-only helper against v2 is rejected.

The [configured runtime](../service_runtime/README.md) rejects old installation
and backup schema digests without rewriting manifests. This standalone helper is
not a complete installation/backup upgrade procedure. It provides no external
anti-rollback ledger, database-administrator sandbox or production acceptance.
Retention, storage, request limits and deployment recovery remain separate work.

## Verification and remaining deployment work

```sh
python3 -m unittest discover -s packages/workspace_admin_api/tests -v
node --test frontend/workspace-admin/tests/*.test.mjs
```

Tests cover platform/member separation, both workspace kinds, lifecycle revisions,
archive restrictions, template-bounded disabled installation, replay after restart,
duplicate concurrent creation, revocation, expiry/persistence rollback and exact
payload rejection. The UI workflow adds real synthetic HTTP/browser interactions.
These tests do not verify real identity login, TLS/proxy behavior, enrollment,
catalog governance, external cancellation, production data or deployment limits.
All [base API deployment requirements](../workspace_api/README.md#required-before-exposing-this-service)
still apply.
