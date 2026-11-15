# Workspace contract draft

This is an implementation contract proposal, not a claim of working isolation.
No server routes, persistence, identity integration, or production migration are
implemented by these documents. Names and endpoints remain draft until an
implementation passes the acceptance gates below.

- [Model and authorization](model.md): ownership, roles, lifecycle, sharing.
- [API and execution contract](api.md): request scope, concurrency, revocation.
- [Acceptance matrix](acceptance.md): positive and adversarial integration cases.
- [Compatibility migration](migration.md): explicit mappings and rollback gates.
- [Synthetic examples](examples.json): fictitious identities and resources only.
- [Original design goals](../workspaces.md).

## Scope of this draft

An instance contains isolated product workspaces and shared workspaces. A
"public workspace" in the product discussion means **authenticated cross-team
collaboration**, not anonymous internet access. This draft names that kind
`shared` to avoid conflating repository visibility with data visibility.

The conservative first implementation requires explicit membership for both
kinds. Automatic organization-wide enrollment, anonymous access, custom roles,
resource moves, writable cross-workspace shares, and emergency data access are
not included. They need separate reviewed contracts before implementation.

The workbench can demonstrate multiple synthetic spaces before these gates pass,
but must label that as a demo, not verified multi-tenant or production security.
Existing demo labels such as `private`/`public` or illustrative space IDs are not
a stable API or migration mapping. The draft `product`/`shared` kind names express
intended policy; code and persistent schema must be reconciled explicitly before
adopting this contract. Labels must never imply anonymous access.

## Implementation increments

The [in-memory workspace policy core](../../packages/workspaces/README.md) is an
independent server-side implementation slice with negative and concurrent tests.
It is not connected to the demo HTTP routes, real authentication or persistence.
The API, sharing and durable isolation gates above remain proposals/unverified
until their respective implementation and integration tests pass.
