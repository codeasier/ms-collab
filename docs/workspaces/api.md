# API and execution contract

Draft only. The routes below are proposed, not deployed. Implementations must
follow [model authorization](model.md) and [acceptance gates](acceptance.md).

## Canonical scope and response rules

Workspace data routes use `/api/v1/workspaces/{workspace_id}/...`. The path is
input, not proof of membership. The server constructs an immutable verified
scope after authentication and authorization; it never trusts a client header,
cookie, query parameter or body field to select another workspace. A redundant
body/header workspace ID that disagrees with the route is rejected, not ignored.
No fallback to the current UI tab, a default space, or the shared space.

- Unauthenticated: `401`; authenticated but unknown or unauthorized scoped object:
  indistinguishable `404`. An authorized member lacking an operation capability
  receives `403` without extra hidden-resource detail.
- Invalid shape or mismatched scope: `400`. A visible disabled module/lifecycle
  conflict: `409`; optimistic precondition failure: `412`; missing required
  mutation precondition: `428`. Authentication and scope checks precede diagnostics
  that could reveal hidden entities. Return no upstream body, token or raw stack.
- Collections are filtered before pagination, counts, facets and aggregation.
  Cursor tokens bind workspace, principal, action, filters and authorization epoch;
  reuse in another context fails. An empty authorized list is not an error.
- Responses contain their `workspace_id` and revision; list envelopes identify
  scope. UI must drop stale responses after a space switch, but server enforcement
  remains mandatory. Private responses are not stored in shared browser caches.

## Proposed endpoints

| Endpoint | Purpose and required authority |
| --- | --- |
| `GET /api/v1/workspaces` | Current principal's visible memberships only; no global counts. |
| `POST /api/v1/workspaces` | Platform operator; creates space and active initial admin atomically. |
| `PATCH /api/v1/workspaces/{w}` | Workspace admin edits labels; platform-only lifecycle fields use separate control routes. |
| `GET /api/v1/workspaces/{w}/memberships` | Workspace admin, with minimum necessary identity fields. |
| `POST /api/v1/workspaces/{w}/invitations` | Workspace admin; verified target identity and bounded workspace role. |
| `PATCH /api/v1/workspaces/{w}/memberships/{m}` | Workspace admin; ETag and last-admin protection. |
| `POST /api/v1/workspaces/{w}/admin-transfers` | Atomic replacement of current admin with eligible active member. |
| `GET/PATCH /api/v1/workspaces/{w}/modules/{key}` | Member sees capability status; only admin changes validated config/state. |
| `GET/POST /api/v1/workspaces/{w}/connections` | Admin manages approved bindings; never return secrets or grant new provider authority. |
| `GET /api/v1/workspaces/{w}/modules/{key}/resources` | Capability-checked collection; module may expose a more specific resource type. |
| `GET/PATCH /api/v1/workspaces/{w}/modules/{key}/resources/{id}` | Composite owner lookup; PATCH needs resource write capability and revision. |
| `POST /api/v1/workspaces/{w}/tasks` | Authorized module action; server validates every referenced resource and binding. |
| `POST /api/v1/workspaces/{w}/shares` | Source admin proposes a projection to a mutually approved target. |
| `POST /api/v1/workspaces/{w}/incoming-shares/{s}/accept` | Target admin accepts exact offered revision. |
| `POST /api/v1/workspaces/{w}/shares/{s}/revoke` | Source admin; target-side incoming-shares route also permits revocation. |
| `GET /api/v1/workspaces/{w}/incoming-shares/{s}/projection` | Authorized target reader; active accepted grant checked on every read. |

Platform-only suspend/archive operations use a distinct control surface. They
must not reuse workspace-admin label updates. Resource delete, invitation
acceptance, connector secrets provisioning and provider-specific actions require
their own reviewed schemas; this table does not implicitly authorize them.

Targets for sharing are explicitly registered/approved through platform
governance; a source admin cannot enumerate private workspace names to find one.
Only minimal names/IDs for approved destinations are returned. Share creation
with an inaccessible target fails without confirming that target exists.

## Mutation concurrency and audit

All updates carry `If-Match` with the last observed revision. State transition,
authorization version and data write are serialized/conditionally committed so
that a stale permission decision cannot commit after revocation. New operations
use an idempotency key scoped to `(workspace, principal, route, action)` and a
request digest. Same key with a different request is `409`; the replay response
is reauthorized and cannot disclose a result after access was revoked.

Audit records include actor, verified space, action, object ID/revision, result,
request ID and relevant policy version, not raw secrets/content. Cross-space
sharing emits source and target audit records with access-controlled redaction.
Data mutations and audit intent use a durable transaction/outbox; dropping audit
because the sink is unavailable is not success. Client correlation IDs do not
replace server-issued audit IDs.

## Asynchronous and non-HTTP paths

- Workers carry immutable workspace/principal/module/resource references, not
  arbitrary paths or a process-global "current workspace". Validate permissions
  before dequeuing, before external side effects, and before publishing results.
  Cancellation on revoke is best-effort after an external side effect; record
  the residual outcome rather than claiming rollback. Never retry as an admin.
- SSE/events authorize connection and each emitted item; revoke invalidates
  active subscriptions and replay cursors. Pending private events must not be
  emitted after the revoke barrier. Heartbeats cannot expose hidden task counts.
- MCP/API keys share the same policy service and scoped resource lookups. A key
  cannot set a different workspace in tool arguments. Capabilities are the
  intersection of principal membership, key limits and module policy.
- Search, export, downloads, indexes, snapshots and assistant history/evidence
  enforce the same scope. Signed URLs require an explicit bounded revocation
  design; the strict initial contract uses an authorized download gateway.
- Agent filesystem/worktree and tool credential isolation must bind workspace
  plus user/session; traversal, symlink and stale-worktree reuse are rejected.
  Logical IDs or directory prefixes alone are not a filesystem sandbox.
- A connector shared by infrastructure is still separately bound and scoped per
  workspace. Upstream OAuth/token scopes remain an additional upper bound.
