# Workspace policy reference

An independently testable **server-side policy core**, following the
[workspace contract](../../docs/workspaces/README.md). Python 3.12+ standard library
only. The original `WorkspaceService` is in-memory; an optional
[SQLite adapter](SQLITE.md) adds local transactions and restart persistence.
No HTTP routes, login, providers, workers or secrets are added.
The existing loopback demo does **not** use this service yet.

## What this increment implements

- Product/shared spaces use the same explicit membership policy. Shared does not
  mean anonymous access or aggregation of other spaces.
- Per-space viewer/developer/admin roles and per-module read/write capabilities.
  Platform operators can govern workspace lifecycle and create a space with an
  explicit initial admin, but cannot read existing spaces or self-enroll there.
- Immutable, composite `(workspace, module, resource)` identities and filtered
  counts/pagination. The small text resource is a reference model, not a migrated
  board, artifact, pipeline or assistant store.
- Optimistic revisions on updates, active-admin invariant and atomic admin
  transfer. Concurrent demotion cannot remove the last eligible admin.
- Immediate deny on revocation, module disabling, or workspace suspension;
  archived spaces permit authorized retained local reads and only reducing/revoking
  existing membership authority, so archived readers can still lose access.
  New grants, promotions and admin transfers are denied in archives. Re-enabling a
  module cannot resurrect revoked memberships.
- Reauthorization under the same process lock as each reference resource read or
  update. Metadata-only audit events are appended in memory with updates.

## Trusted boundary

A real adapter must authenticate a **current** principal and supply its internal
ID. Do not wire `actor_id` directly to a request parameter/header or let a client
construct the bootstrap directory. Identity lookup is by the unique issuer and
subject pair, never email or display name. This package does **not** authenticate
anyone, refresh identity-provider status, or protect against malicious code in
its own process.

`authorize(actor_id, workspace_id, module_key, action)` is the integration seam.
Its `Decision` is an observation, **not a reusable token or verified permission**.
This service's `read_resource`, `list_resources` and `update_resource` always check
again while holding the same lock as the operation. An external store cannot
safely use `authorize()` then perform an unlocked operation and claim revocation
safety; it needs a shared transaction/version barrier. Never hold this process
lock across network or external side effects.

Inputs are trusted immutable model records. Returned snapshots cannot be changed
through normal field assignment. Error status/code pairs follow the draft
contract: unknown and unauthorized space/resource return the same `404`; a
nonmember receives no shape/revision diagnostics about hidden objects. A future
HTTP adapter must first validate scalar input types, authenticate and reconcile
canonical route/body scope, rather than exposing Python exceptions.

## Run tests

From the repository root:

```sh
python3 -m unittest discover -s packages/workspaces/tests -v
```

All fixtures are synthetic. Concurrency cases use barriers/events, not sleeps.
[Tests and contract mapping](tests/README.md) document their limits. Public CI runs
these tests as a required-to-merge project check, independently of the browser
demo; this wording does not assert repository branch rules are configured.

## Intentionally not implemented

- Real authentication, API keys, HTTP/UI integration or deployment readiness.
- Enrollment/invitation acceptance or directory synchronization. Memberships are
  trusted bootstrap inputs; edits can revoke or change an existing active member,
  but cannot add a member or reactivate a revoked membership. This avoids treating
  an invitation or a caller-supplied identity as membership.
- Arbitrary module configuration, capability expansion or connector bindings.
  A separate [administration adapter](../workspace_admin_api/README.md) supplies a
  platform-approved catalog for disabled installation; the policy's installation
  method accepts capabilities only from trusted server code, never HTTP claims.
- Explicit sharing, caches/cursors, idempotency, queues/SSE/downloads/exports,
  resource-specific restrictions, filesystem/Agent isolation or provider scope.
- External transactions/outbox, read/denial audit and request correlation.
  The optional SQLite adapter provides local transactional persistence; its
  successful-mutation metadata is not a complete compliance audit or external
  side-effect barrier. See its precise [durability limits](SQLITE.md).
- Automatic rollout to the existing demo or any existing service.

This is a policy implementation slice, **not proof of end-to-end multi-tenant or
production isolation**. Integrations must use the contract's negative tests and
report their own evidence. No original repository code, production data or
private operations history is included.
