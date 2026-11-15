# Authorized community snapshots

A configured WSGI adapter connecting the GitCode read-only provider to current
workspace authorization and a content-reviewed board. It supplies HTTP APIs, not
a default login, a production listener, an anonymous public board, or a deployment.
The separate synthetic demo remains unchanged.

## Three distinct permissions

A trusted `ConnectionPolicy` binds one `Connection` (workspace, connection ID,
repository and bounded provider limits), an opaque `credential_ref`, and three
explicit principal-ID allowlists:

- `reader_ids`: may see **only content explicitly approved for this connection**,
  while currently a workspace member with enabled community read capability.
- `refresher_ids`: may request a provider observation only while currently an
  active workspace admin with community write capability. Refresh does not share
  data and does not give the requester approval or board-reading permission.
- `approver_ids`: may review raw normalized observations only while currently an
  active admin with community read capability, and may change the sharing set
  only with community write capability. Approval is separate from refresh.

A workspace admin has no implicit connection grant. An explicit connection grant
has no power without current workspace membership, role and module capability.
Principal IDs come from the existing trusted verifier binding, never HTTP actor
fields or caller-selected headers. All three permissions remain independent.
A workspace of kind `shared`, a name such as `public`, and an upstream public
repository bypass **none** of these checks. There is no anonymous route or union
of other workspaces' data.

## Explicit sharing policy

Provider-visible data is not automatically shared. Initial and refreshed
snapshots are private to the configured reviewers; the ordinary board starts
empty. An approval records SHA-256 of the exact workspace/connection/repository
and the entire normalized item (including kind, number, title, state, update time
and labels). A changed item must be reviewed again. Matching timestamps do not
make changed fields trustworthy; ambiguous or stale observations are rejected.
Approvals cannot be copied to another scope. This is deliberately conservative,
not an automatic interpretation of GitCode `visibility_reason` or a legal/data
classification decision. Reviewers must inspect the content and have authority
to share it with the configured reader audience.

An approval request replaces the **entire** connection approval set, at most 200
items, against the exact revision the reviewer saw. A UI using paged review must
preserve selections outside the visible page and verify one common revision.
Sending an empty set explicitly revokes all sharing. A failed/stale request does
not change the existing set. Changing the trusted connection policy or audience
clears the snapshot and approvals; it never exposes prior observations to new
readers or reviewers automatically.

## Same-database transaction boundary

Community snapshots, configuration and approvals live in a dedicated
`community_connections` table in the **same** initialized `SQLiteWorkspaceService`
database as principals, memberships, modules and audit. They are not generic
`Resource` rows: generic resource list/read/write endpoints cannot bypass these
sharing rules or mutate the community data. No request reads the separate
`community_sync.SnapshotStore` database, and this API does not automatically
import it. The old offline adapter remains available for trusted jobs, but an
unauthorized second-database join is not accepted as transactional isolation.

Reads resolve current identity, workspace/module policy, connection grants,
approvals and the selected snapshot within one SQLite transaction. Results
materialize there and use `Cache-Control: no-store`. They linearize at the read
snapshot; revocation cannot retract an already authorized concurrent response.

Refresh has two phases with no network inside a database lock:

1. An authenticated transaction verifies current refresh authority and `If-Match`.
2. A trusted factory callback supplies a `BoundCredential` for exactly that scope
   and credential reference. The provider performs its bounded, GET-only scan.
3. A new `BEGIN IMMEDIATE` transaction resolves identity and authorization again,
   checks the original principal binding and unchanged connection revision,
   validates monotonic observations, then stores the new snapshot, retained
   exact-content approvals and audit entry together.

Membership revocation/demotion, module/workspace changes, expired assertions,
connection reconfiguration, competing approvals or another refresh during the
network phase prevent publication. Errors leave the old observation timestamp
and approvals intact. A GET already authorized before revocation cannot be
undone, but its result is not published after a failed second authorization.
The factory verifier's assertion expiry check also runs immediately before the
single SQLite commit; this is not distributed revocation with GitCode or a
promise that an upstream token remains valid after the provider responds.

The trusted `_transaction` / `_authenticated_transaction` `with_connection` seam
passes the existing SQLite connection to **internal adapter code only**. No
request selects SQL, a database path, callback or credential reference. Such
callbacks must not commit/rollback or expose the connection. Missing tables,
invalid stored records and failed persistence fail closed; there is no fallback
to a separately loaded snapshot or cached authorization.

## Configuration

Use `initialize(store, policies)` once on an already initialized policy database
containing community module instances. It refuses an existing table and does not
import production state. `replace_policy(store, policy, expected_revision=...)`
is a trusted operator integration helper, not an HTTP management route. It
atomically invalidates old data and approvals. All configured IDs must refer to
known principals; current membership is still checked on every request.

Construct `CommunityApplication` with the existing explicit `store`, `providers`
and `provider_key`, plus a trusted `credential_loader(scope, credential_ref)`.
The loader must return `BoundCredential` with exactly the requested scope and
must obtain only credentials the operator authorized for that connection. It
must not log or persist secret values. No environment/CLI/production credentials
are discovered by the application. Omitting the loader disables refresh; it does
not enable a default provider token. Test verifiers and loaders stay under tests.

Each normalized snapshot is bounded to 1 MiB in this adapter. Larger observations
are rejected without changing current state; reduce scan bounds intentionally
rather than silently truncating. The provider's result remains an observed
subset, not proof of complete repository coverage or upstream lifecycle events.
The policy SQLite reference adapter materializes policy rows and is not a
production-scale multi-tenant database design.

## HTTP contract

Prefix: `/api/workspaces/{workspace}/community/{connection}/{owner}/{repository}`.
All responses use the existing `{"data": ...}` / safe `{"error": ...}` envelope.
Only the configured bearer verifier determines the current actor.

| Method | Suffix | Behavior |
| --- | --- | --- |
| GET | `/capabilities` | Current `scope`, `revision`, and boolean `items`, `review`, `refresh`, `approve` capabilities; no grant means 404 |
| GET | `/items` | Approved-only board projection with scope, revision and observation time |
| GET | `/review` | Authorized reviewers' candidate projection, each row with `fingerprint` and `approved` |
| POST | `/refresh` | Exact JSON `{}`; observe and publish, without sharing new content |
| POST | `/approve` | Exact JSON `{"fingerprints":[...]}`; replace the entire approval set |

Both POSTs require `If-Match: "<revision>"`; missing returns 428 and stale returns
412. GET items/review accept `kind`, `state`, `query`, `limit` (1..100) and `offset`.
A connection without a snapshot returns `available:false`, `items:[]`, `scope`
and `revision`, not fabricated fresh observations. Capability discovery does not
reserve authority; every action rechecks current policy. The API never returns
credential references, tokens, raw response bodies or private provider URLs.
It cannot itself determine whether a reviewed title contains sensitive content.

## Verification and remaining limits

```sh
python3 -m unittest discover -s packages/community_api/tests -v
python3 -m unittest discover -s packages/community_sync/tests -v
python3 -m unittest discover -s packages/workspace_api/tests -v
python3 -m unittest discover -s packages/workspaces/tests -v
```

Synthetic tests cover current membership/roles, explicit per-connection grants,
content-specific approval, shared-workspace separation, scope collisions,
revocation/demotion/expiry/config changes during network work, CAS races,
SQL/expiry rollback of both data and audit, generic resource-route isolation,
corruption, oversized responses and real loopback HTTP. No live provider account,
real data, real identity verifier, deployment or production migration was used.
A real verifier, secure credential loader, TLS, bounded server reads, rate limits,
connection administration and operational review are still required before
exposure. The companion board UI is a separate integration, not proof of these
production prerequisites.

[Tests](tests/README.md) · [Provider contract](../community_sync/README.md) ·
[Workspace API](../workspace_api/README.md) · [Documentation](../../docs/README.md)
