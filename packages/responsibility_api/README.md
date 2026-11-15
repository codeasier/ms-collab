# Scope-authorized responsibility service

An optional configured WSGI service for responsibility config and read-only work
candidates. It uses a real supplied verifier and current policy database, never
a fake production identity. The synthetic demo and existing services are not
rewired. There is no listener, provider discovery or automatic workflow here. An optional
[authorized UI composition](../responsibility_ui/README.md) is separate.

## Explicit setup and authority

On the existing initialized `SQLiteWorkspaceService`, configure a community
connection and enabled `community` **and `responsibility`** modules for the space.
Use `initialize(store, policies)` exactly once to create the dedicated versioned
table; GETs never initialize or migrate it. Each `ResponsibilityPolicy` contains
one `Scope(workspace_id, connection_id, repository)` and explicit `reader_ids`,
`editor_ids`, `publisher_ids`. No role name grants these automatically.

Construct `ResponsibilityApplication(store=..., providers=..., provider_key=...)`
using the same authoritative directory and reviewed verifier as the base API.
There is no default connection, credential, global directory or principal lookup
from request headers. All operations require current identity, membership, both
module read capabilities and underlying community reader grant, plus explicit
responsibility reader grant. Editors/publishers additionally require their
separate grant, active workspace administrator role and responsibility write
capability. Reader authority never implies mutation or publication.

Platform operators, owner/backup/committer field labels, public repositories and
shared workspace kind bypass none of these controls. No all-workspaces fallback
exists. Updating a trusted responsibility policy through `replace_policy` uses
CAS and clears its previous config/evidence before a new audience can see it.
This is a trusted integration helper, not an HTTP grant-management endpoint.

## HTTP contract

Prefix: `/api/workspaces/{workspace}/responsibility/{connection}/{owner}/{repo}`.
Success is HTTP 200 with `{"data":...}`. Safe errors contain no provider, SQL,
credentials, raw request or exception details. The base bearer/no-store framing
and final current-identity checks apply to all operations.

| Method | Suffix | Behavior |
| --- | --- | --- |
| GET | `/capabilities` | Current explicit read/edit ability; writes still reauthorize |
| GET | `/config` | Scope, revision, config version, local identity namespace and fields |
| PUT | `/config` | Exact `{"fields":[...]}`; explicit editor authority and `If-Match` |
| GET | `/candidates` | `view=mine|all|field|unassigned`, optional `field_id`, `offset`, `limit` |

Configuration bodies are bounded to 64 KiB and reject duplicate/unknown keys.
Owner IDs must resolve to current active members of the same space. No email,
REST-ID coercion or directory enumeration is provided. Existing field IDs cannot
be deleted by replacement in this slice. PUT uses the record revision the caller
read: missing returns 428, stale 412. Changes increment config/record versions
and metadata audit atomically. A timeout is not proof of failure; read current
state and review new intent before another write. There is no automatic retry.

Candidate inputs include **only current exact-content-approved community items**.
Unapproved titles and their counts/paths do not appear. Observations are selected
by an exact scope/item fingerprint and current community revision. Any community
refresh, approval or configuration revision invalidates all prior path evidence
until republished; missing evidence is incomplete, never empty-complete. Even a
complete result is `head_base_rechecked_unpinned`, not review/merge authority.
The [core contract](../responsibility/README.md) defines candidate filters and
explicitly unimplemented Issue identity mapping and workflow semantics.

## Prepared observation integration, not a public upload

`application.publish_prepared(credential, scope, observations,
expected_revision=..., community_revision=..., observed_at=...)` is a **trusted
server integration**, not an HTTP endpoint. It takes an opaque `Credential`, not
an actor ID. The configured verifier authenticates it; current binding, publisher
and reader grants, active administrator role and module permissions are resolved
inside the same SQLite transaction as publication/audit. Callers must acquire and
review path text for the exact configured reader audience before invoking it.
This API cannot determine whether a path reveals confidential project metadata.
There is no implicit publication from provider token visibility or config edits.

Each prepared record has only an approved open-PR item fingerprint and validated
`PathEvidence`. At most 200 records and 1 MiB serialized data are allowed. Issues,
unapproved targets, other scopes, invalid dates, future/backwards observations
and stale revisions are rejected. The timestamp must cover the current community
snapshot. No patch text, comments, raw provider response, user profile, token or
upstream URL is retained. There is **no live path acquisition** or external Issue
identity mapper in this slice. Tests construct only synthetic observations.

The data type is not an authenticity proof: trusted code can manufacture input.
It is intentionally never an approval gate or executable transport. The caller
must not pass client claims or manufacture `complete=true` from a file count.
The pure normalizer checks the supplied page/end/recheck/count contracts; it does
not itself verify a remote request occurred or solve head A→B→A consistency.

## Transactions, evidence and remaining limits

The dedicated table is foreign-keyed to the exact community connection, not
exposed through generic resource endpoints. Config/observation revisions, policy,
current approvals, membership and audit are read or changed in one transaction.
Concurrent CAS losers, revoked/demoted identities, module disabling and assertion
expiry before commit fail closed. Audit or persistence failure rolls back the
whole change. Metadata uses scoped hashes and revisions, not bodies or secrets.
Reads linearize at their authorized snapshot; already delivered data cannot be
retracted. No network operation occurs while the SQLite lock is held.

Schema setup/read validates the exact versioned STRICT table, checks and foreign
key, rather than silently adopting an unknown table. The reference store still
materializes rows in memory; production rate/size/retention limits and scalable
queries are separate work. Audit is successful-mutation metadata, not a complete
read/denial audit. Real verifier, TLS/proxy/server body/time limits and independent
production acceptance remain required. No old OBS/production state is touched.

```sh
python3 -m unittest discover -s packages/responsibility/tests -v
python3 -m unittest discover -s packages/responsibility_api/tests -v
```

[Tests](tests/README.md) · [Core/migration](../responsibility/README.md) ·
[Community sharing](../community_api/README.md) · [Documentation](../../docs/README.md)
