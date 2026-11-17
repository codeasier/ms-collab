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
  transfer. Concurrent demotion cannot remove the last eligible admin. Eligibility
  requires the durable membership pin, not only an active local-ID row.
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

## Initial-admin selection precondition

Both services expose `initial_admin_selection(actor_id, initial_admin)` for a
current operator to inspect one active local target. It returns exactly
`{"initial_admin": ..., "precondition": ...}`. Creating a new workspace requires
that value as `create_workspace(..., expected_initial_admin_selection=value)`.
Omitting it, including `None`, returns 428; malformed values return 400 and a
changed/unavailable selection returns 412. There is no trusted-caller fallback
that captures the current target inside creation. Explicit fresh constructor/
`initialize()` bootstrap remains a separate trusted operation.

The `ias1-` plus 64-lowercase-hex value deterministically compares version,
purpose, realm and both actor/target identity pins. Comparison, current operator
checks, workspace creation, the new membership pin and audit share the same
lock. The SQLite adapter acquires its writer lock before loading and comparing.
The value is not a credential, permission, unforgeable proposal, external identity
verification or evidence of human intent. It has no expiry/consumption and stays
equal across a normal restart if those identities are unchanged. It does not
detect an entire old database being restored with identical pins.

A trusted UI/adapter must retain the explicitly reviewed value, not silently
fetch a new one when a write conflicts. The
[HTTP administration contract](../workspace_admin_api/README.md#initial-admin-identity-selection)
defines the single-target GET, separate request header and old-record exact
replay compatibility. Core methods themselves do not add HTTP authentication or
idempotency. Invitations/other selections, legacy grant recovery and identity
transitions remain separate gates.

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
- Directory synchronization or anonymous enrollment. Core memberships remain
  trusted inputs; ordinary edits cannot add or reactivate a revoked member. The
  optional [invitation adapter](../workspace_invitations/README.md) now performs
  explicit identity-addressed acceptance and membership insertion atomically; it
  does not authenticate unverified identities or change the core edit contract.
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

## Closed principal authority and persistent grant ownership

Principal generation/authority epoch and SQLite realm are available to reviewed
consumers. Policy schema v3 adds separate membership/operator authority records
without changing the public `Membership` fields. A grant must match realm, local
principal ID, issuer, subject and generation; stale admins/operators do not count
as eligible authority. Explicit trusted fresh bootstrap captures pins, whereas
strict persisted loading never captures or repairs them.

Explicit offline v1/v2 policy upgrades preserve old records and add all-NULL
grant tombstones. They do not adopt legacy rows as current grants. Missing or
partial/corrupt sidecars reject storage; complete stale pins deny the affected
grants. Retained workspaces without eligible admins remain structurally loadable,
not authorized, and valid zero-operator stores do not gain an operator.

Ordinary member edits preserve pins; stale grants may be revoked but not promoted
or repaired. Admin transfer requires a current replacement. Invitations may
capture a genuinely new member, or re-enroll a revoked member with its existing
current pin, never repin a stale or NULL prior grant. Explicit recovery/regrant is
not provided by this slice. Historical audit actor IDs remain historical local
references, not proof of a current identity.

Identity mutations remain unconditionally unavailable in both service
implementations, with database guards on the persisted directory. No flag enables
rebinding, disable/reenable or provisioning. The [foundation contract](../../docs/principal-authority.md)
describes explicit schema upgrades, retained legacy grants and gates before a
future identity-administration implementation.

`authority.py` supplies strict pure capture/validation/current-binding helpers for
explicit scoped grants. It does not mutate principals or authenticate requests;
legacy decoders must never call capture as a fallback. First use is the
[community grant adapter](../community_api/GRANTS.md).

## Bounded mutation counters

Policy record revisions, the policy epoch and audit sequences are exact integers
in `1..9007199254740991` (the JavaScript safe-integer range). Model inputs and
revision preconditions reject booleans, floats and larger values. Before any
core mutation, the complete operation budgets its changed record revisions,
policy epoch increment and all success-audit slots. Exhaustion returns
`409 counter_exhausted` without changing records, epoch or audit. In particular,
admin transfer budgets both members and both events before either promotion or
demotion. Ordinary same-value membership edits still advance revisions; they do
not become identity no-ops or reset exhausted counters.

Reads of valid records at the maximum remain possible. An exhausted policy epoch
blocks policy mutations but not otherwise-authorized reference-resource writes
that do not change that epoch. A saturated membership cannot be edited, revoked
or re-enrolled by wrapping/clamping its counter. There is no automatic reset,
compaction or recovery migration; operator recovery needs a separately reviewed
procedure. Authorization, last-admin and archived-space restrictions still apply.
See [SQLite compatibility](SQLITE.md#bounded-counter-compatibility) and
[counter regression evidence](tests/README.md#bounded-counter-regressions).
