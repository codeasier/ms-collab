# Transactionally authorized artifact metadata API

A configured read-only WSGI adapter now connects the artifact catalog core to the
existing SQLite workspace policy and trusted identity verifier. It supports real
HTTP request/response handling, not a new login provider or production service.
There is no default verifier, listener, file download, provider connection, import
job or UI. The existing synthetic demo is unchanged.

## Authorization and metadata boundary

A trusted factory supplies the same explicit store/provider configuration as
[WorkspaceApplication](../workspace_api/README.md). Protocol verification occurs
before the transaction. Current issuer/subject binding, membership, workspace
state, `artifacts` module read capability, catalog and named-view records all
come from one SQLite snapshot. Response materialization and the last assertion
expiry check stay within that transaction. The inherited `no-store`, error
redaction and bearer-only authentication behavior apply. Headers, query/body
fields and a selected named view cannot select an actor or grant access.

Module read permission currently authorizes **all provisioned catalogs within
that workspace's artifacts module**. This is not per-bucket ACL enforcement or
external connector authority. Different workspace, connection and bucket IDs are
never fallback alternatives, and the shared workspace is not a union. A missing
or unavailable named view fails rather than broadening the query. Already
materialized authorized reads are not retracted by a later revocation. The
SQLite/API snapshot and pre-COMMIT expiry boundaries are documented in the
[workspace API](../workspace_api/README.md).

## Provisioning and limits

The trusted `catalog_resource(snapshot)` and `view_resource(view)` helpers encode
validated pure-core objects into ordinary `artifacts` policy resources. IDs use a
digest of connection/bucket/view identity to avoid delimiter collisions; every
stored record's full scope is independently validated before use. Catalog and
view are loaded through `read_resource` only after current authorization.

These are bounded local metadata records, not a scalable index: each JSON record
must fit the existing 10,000-character resource limit (ASCII encoding). Larger
inventories require a dedicated transactional catalog store, not a raised HTTP
limit or bypass of scope checks. No URLs, credentials, raw logs or file bodies
are supported in the schema. Unexpected fields, duplicate fields, unsupported
versions or scope mismatches in stored metadata return a redacted storage error.
Existing generic resource updates remain subject to their own write/revision
checks; malformed catalog edits fail closed at this adapter. This batch adds no
upload, catalog mutation, view-management, sharing grant or download endpoint.

## HTTP contract

Base: `/api/workspaces/{workspace}/artifacts/{connection}/{bucket}`. IDs use
ASCII alphanumerics plus `.`, `_`, `-` and are at most 128 characters, starting
with an alphanumeric. Named view IDs use the same routable subset. All endpoints
require the configured bearer verifier. Success uses `{"data": ...}`; errors
use `{"error": "safe_code"}`.

| Method | Suffix | Input |
| --- | --- | --- |
| GET | `/quarters` | No query; returns exact scope, quarters and catalog revision |
| POST | `/query` | JSON `quarter`, `definition`; optional `page`, `page_size` |
| GET | `/views/{id}` | Required `quarter`; optional `page`, `page_size` |

`POST /query` is read-only. It requires explicit length and JSON content type,
rejects unknown/duplicate fields and is bounded at 16 KiB. Pages are 1–1,000,000,
page sizes 1–100. Definitions use the existing [artifact core](../artifacts/README.md)
contract. Results include exact scope, observation time, quarter availability,
catalog revision, and view revision where used. Missing quarters produce an
explicit empty result, not a latest-quarter fallback.

Synthetic request body (not a credential):

```json
{"quarter":"2026Q1","definition":{"path":"builds/","files_only":true,"sort_by":"size","order":"desc"},"page":1,"page_size":20}
```

## Validation and remaining work

```bash
python3 -m unittest discover -s packages/artifacts_api/tests -v
python3 -m unittest discover -s packages/workspace_api/tests -v
python3 -m unittest discover -s packages/artifacts/tests -v
```

Tests use temporary databases, a test-only verifier and synthetic inventories,
including one real loopback HTTP request. They cover cross-scope and shared-space
negative cases, denial before parsing metadata, current revocation, disabled
modules, archived reads, scope-corrupted records, named-view fail-closed behavior,
query limits, snapshot consistency and expiry during result construction.

A real reviewed provider, TLS/proxy/server limits, enrollment and management UI,
provider ingestion, durable scalable metadata indexing, sharing authorization,
controlled downloads and deployment acceptance remain separate work. Do not
expose this adapter using the test verifier or attach real data to the demo.

[Package tests](tests/README.md) · [Documentation index](../../docs/README.md)
