# Model and authorization

Status: proposed contract v0.1; see [scope](README.md). All persisted identifiers
are server-generated opaque IDs. Slugs and display names are mutable labels,
never authorization keys. UUID syntax alone is not authorization.

## Entities and invariants

| Entity | Required identity and relationship | Invariant |
| --- | --- | --- |
| Principal | `principal_id`, identity `issuer` + `subject`, status | External identity pair is unique; no email/display-name matching for access. |
| Workspace | `workspace_id`, slug, `kind=product/shared`, state, revision | Kind does not grant access. IDs never reused after archival. |
| Membership | workspace + principal, role, state, revision | At most one active membership per pair; invitation is not membership. |
| Module instance | workspace + module key, state, config revision | One instance per pair in v0.1; installed module catalog is platform-controlled. |
| Connection binding | workspace + binding ID, provider, approved capability set | References secret storage, not secret values; authority cannot exceed provider scope. |
| Resource | workspace + module + resource ID, immutable owner, revision | All reads, relations and writes constrain owner workspace. No unscoped `get(id)`. |
| Source identity | workspace + binding + upstream type + upstream ID | Same external ID/path in two spaces cannot collide or imply shared access. |
| Share | source resource/revision + target workspace + projection, state, revision | Only a reviewed, read-only projection; never a token, connection or execution authority. |
| Task | workspace + module + principal + resource/binding references + authorization epoch | Submitted authority is recorded, but authorization is rechecked at execution. |

All foreign keys between owned entities include workspace ID. Shared database
tables may be used, but repository APIs must require a verified scope and enforce
composite ownership. Database layout alone is not proof of isolation. Caches,
search indices, object keys, journals, idempotency keys, rate limits and job
deduplication include workspace ID and relevant principal/connection identity.
Do not expose secret references, internal paths or privileged connector metadata
in general member responses.

## Roles and authority

The initial fixed roles are `viewer`, `developer`, `admin`. Membership roles are
local to one workspace; membership in another never increases authority here.
The same rules apply to product and shared workspaces.

| Operation | Viewer | Developer | Workspace admin | Platform operator only |
| --- | --- | --- | --- | --- |
| Read allowed resources and read-only shared projections | Yes | Yes | Yes | No |
| Mutate module resources / submit jobs | No | If module capability allows | If module capability allows | No |
| Manage this space's members, modules and approved connection bindings | No | No | Yes | No |
| Propose/revoke outgoing shares; accept/reject incoming shares | No | No | Yes | No |
| Create/suspend a workspace; maintain module catalog and platform quotas | No | No | No | Yes |
| Read private evidence / use a connector just by holding platform role | No | No | No | No |

"Yes" still requires an active membership, the workspace lifecycle to permit the
operation, the module's relevant capability, resource ownership or valid share,
and applicable provider authority. Active spaces permit eligible reads/writes;
archived spaces permit only authorized reads of retained local resources.
Suspended spaces deny business-data reads and writes. Archived/suspended spaces
never serve incoming or outgoing shares, regardless of earlier acceptance.
An admin cannot grant platform operator status, expand a platform-approved
connector scope, bypass a resource policy, or disable mandatory audit controls.
The platform operator can create a workspace and nominate an initial admin from
the verified identity directory, but may not silently self-enroll in existing
spaces. Platform recovery or emergency access is out of scope and deny-by-default.

Deleting/demoting/suspending the last active admin is rejected atomically. Admin
transfer activates the replacement and demotes the previous admin in one
transaction. Pending invitations or disabled principals do not satisfy this
invariant. Invites are scoped, expiring and single-use, bound to a verified
identity; no global user-directory enumeration for workspace admins.

## Authorization decision

For ordinary resource access evaluate:

1. Authenticate a current principal. API keys carry a specific principal,
   workspace and bounded module capabilities; global wildcard keys are excluded.
2. Resolve the workspace from the canonical route and validate active membership.
   Unknown and unauthorized workspaces are indistinguishable to the caller.
3. Check workspace lifecycle, module instance state and action capability.
4. Fetch the resource using the verified workspace scope; check resource-specific
   restrictions and connection binding within that same scope.
5. Execute and audit under that scope. Mutation and permission checks must not
   race with a membership/module/share change unnoticed.

A shared projection is the only exception to source-workspace membership: the
reader must be authorized in the target space and hold an active accepted grant
to the exact allowlisted projection. It is never a normal source-resource fetch.

## Lifecycles

- Workspace: `active -> suspended -> active`, or `active/suspended -> archived`.
  Suspension denies data access and execution; only a minimal platform recovery
  control surface remains. Archival stops execution and permits authorized
  read-only access according to retention policy. A separate membership control
  path remains available to current admins for existing-member revocation or
  role reduction, including revision checks and last-admin protection; archival
  must not make retained data access irrevocable. New grants, promotions and
  admin transfers are denied in an archive. Reactivating an archive and
  permanent deletion are deferred; no implicit delete or public fallback.
- Module: `disabled -> enabled -> disabling -> disabled`. Only admins can change
  configuration. Disabling rejects new module actions immediately, cancels queued
  tasks, signals running tasks to stop, and denies module data/download endpoints
  once disabled. Current members may read an exact installed module's minimal
  lifecycle projection: `workspace_id`, `key`, `state`, `revision`. This remains
  readable for disabling/disabled modules and in archives, but not suspended
  workspaces. It is not effective resource permission, configuration, a module
  directory, host mounting or provider readiness. Existing full-module inspection
  permissions are unchanged; admin-only listing/configuration remains separate.
  An external side effect already executed cannot be undone by hiding the module. This
  observation does not establish that a task-cancellation mechanism exists.
- Re-enabling uses the last approved config after validation. It does not replay
  old jobs, restore revoked shares or silently expand connector permissions.

## Explicit sharing

Use an immutable, versioned projection: the source admin selects an eligible
resource revision and allowed fields, and the target admin accepts that specific
offer. Membership in the source workspace is not then required for readers in
the target workspace. Source connectors are not exposed to the target.

State: `offered -> active` after target acceptance; either side may revoke;
offers expire; rejected/revoked/expired grants cannot be resurrected. A changed
source revision or larger field set requires a new offer and acceptance, not
silent refresh. Suspend/archive of either workspace or disable of either involved
module invalidates serving the projection and requires explicit reapproval on
recovery. No transitive resharing or target-side source mutation in v0.1.

Revoke denies subsequent requests once the revoke operation commits; purge cached
projections, search entries and derived artifacts before reporting completion.
If purge cannot be confirmed, use `revoking` and deny all serving while retrying.
No anonymous/permanent object-store URLs for protected projections. Downloads
already completed, copied text, and an external side effect cannot be recalled;
the UI must not promise otherwise. Raw logs, full assistant sessions, credentials
and unrestricted evidence exports are ineligible until a field-redaction policy
and threat model are separately accepted.
