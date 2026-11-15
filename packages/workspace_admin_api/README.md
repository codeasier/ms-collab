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
`initialize_administration` creates an additive retry-record table in that same
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

Both POST endpoints require a canonical UUIDv4 `Idempotency-Key`. The retry record
is scoped to actor, action and workspace (or platform creation), with a digest of
the exact body. Identical replay returns the current referenced record without
another mutation/audit; a different body with that key returns 409. Install replay
does not restore the original disabled state or re-enable anything.

The operator/admin is reauthorized before any retry record is read, in the same
SQLite transaction as the policy operation. Current identity, active membership,
lifecycle and assertion expiry still apply on replay. Creation/installation,
retry metadata and audit commit or roll back together. Expiry after mutation but
before commit rolls all three back. The inherited commit-wait and upstream
revocation limitations remain; this is not a distributed transaction.

Retry metadata contains IDs and digests, not request bodies or credentials. There
is no automatic pruning. Operators must separately define retention, storage and
rate limits before exposing a service. Clients must retain a retry key across
uncertain responses. The UI retains it only while the connected page remains
open; after disconnect/reload, inspect control metadata before creating again.
No automatic retry is made, and an aborted browser request does not undo an
already accepted mutation.

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
