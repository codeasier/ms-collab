# Transactional workspace API adapter

Status: a configured WSGI application connecting the identity verifier boundary to
SQLite workspace policy. **Not a real login provider, deployable production
server or full multi-tenant application.** The existing local demo is unchanged
and does not use this adapter. All verifier doubles and credentials in this
package live only in [synthetic tests](tests/README.md).

## One request, one protected transaction

- A trusted factory supplies an existing `SQLiteWorkspaceService`, allowlisted
  `Provider` objects and one fixed `provider_key`. There is no default provider,
  runtime server command, arbitrary callback in request data or anonymous user.
- Only `Authorization: Bearer <opaque credential>` supplies credentials. Cookies,
  `REMOTE_USER`, identity/role headers, query parameters and body actor fields
  cannot select the actor. An upstream reverse proxy must not rewrite untrusted
  headers into the configured protocol verifier's authority.
- The configured verifier validates the protocol **outside** the SQLite lock.
  There is no bundled verifier that trusts client claims or unsigned JWTs.
- After `BEGIN` (reads) or `BEGIN IMMEDIATE` (writes), the adapter rechecks the
  assertion's configured issuer/audience and validity. It resolves the current
  `(issuer, subject)` binding from the **same loaded SQLite state** that holds
  membership, module and resource policy. Unknown or inactive bindings fail.
- The policy operation checks scope, membership, module state, role and revision
  in that transaction. An assertion that expires while waiting to begin the
  protected transaction or during evaluation/persistence is rejected; a mutation
  and its audit roll back together. The last clock check is immediately before SQLite COMMIT. COMMIT may
  itself wait, so this is not a promise of validity until commit/response
  completion, nor an atomic transaction with the upstream identity provider.
  No precomputed identity snapshot, `Decision` or client actor ID is authority.
- Responses are materialized in the transaction and marked `no-store`. Reads
  linearize at their SQLite snapshot: a revocation committed before that snapshot
  is observed. This does not retract bytes from an already authorized concurrent
  read or recheck a revocation after the response has begun.

This closes the local directory/policy lookup race for these SQLite operations.
It does **not** implement upstream credential revocation, distributed revocation,
queued work authorization, credential persistence or a durable external identity
administration procedure. Those are separate integration requirements.

## Narrow HTTP contract

All success responses use `{"data": ...}`. Failures use `{"error": "safe_code"}`.
IDs in these routes use ASCII letters/digits plus `.`, `_`, `:`, `-` (up to 200
characters). The adapter deliberately has no dynamic method-dispatch endpoint.

| Method | Path | Result / payload |
| --- | --- | --- |
| GET | `/api/workspaces` | Current member-visible spaces |
| GET | `/api/workspaces/{workspace}/members` | Admin-only member list |
| PATCH | `/api/workspaces/{workspace}/members/{member}` | Exact `role`, `state` string fields |
| GET | `/api/workspaces/{workspace}/modules/{module}/resources` | `total`, `items`; optional `offset`, `limit` |
| GET | `/api/workspaces/{workspace}/modules/{module}/resources/{resource}` | One scoped record |
| PUT | `/api/workspaces/{workspace}/modules/{module}/resources/{resource}` | Exact `text` string field |

Mutations require a quoted positive revision in `If-Match` (for example `"1"`),
explicit `Content-Length` and JSON content type. The 16 KiB input bound, exact
field allowlist, duplicate-key rejection and valid UTF-8 check apply before
mutation. Missing precondition returns 428; stale revision returns 412. There is
no enrollment, workspace creation, arbitrary principal binding, transfer, module
configuration, audit export, bulk update or file upload endpoint in this batch.
Archived spaces retain the existing policy: reads and admin-only authority
reduction remain possible, ordinary resource writes are rejected.

The application emits no CORS allowance, cookie, token, provider error body or
SQL/path detail. It never logs request environments or credentials. Deployments
must ensure their own server/error telemetry does not log those values either.
`Credential` is not secure memory erasure; Python tracebacks may retain objects.

## Required before exposing this service

A reviewed real protocol verifier, TLS, trusted WSGI/proxy header normalization,
request header/body/read timeouts, connection/concurrency limits, rate limiting,
actual identity enrollment/revocation procedures and durable store ownership are
still required. The adapter does not trust cookie sessions; adding one would
require session lifecycle and CSRF protections, not just another header lookup.
Do not import the test verifier into an app factory or connect real data to the
existing demo. This change neither launches a listener nor changes production.

## Verification

```bash
python3 -m unittest discover -s packages/workspace_api/tests -v
python3 -m unittest discover -s packages/identity/tests -v
python3 -m unittest discover -s packages/workspaces/tests -v
```

Tests use generated temporary SQLite stores and a test-only allowlist verifier.
They exercise in-process WSGI, one real loopback HTTP server, forged identity
headers, unknown fields, cross-space access, revocation and re-binding during
verification, lock-wait expiry/revocation, stale concurrent writes, rollback and
safe failure responses. They do not verify any real OAuth/OIDC/JWT provider.

[Identity boundary](../identity/README.md) · [SQLite adapter](../workspaces/SQLITE.md)
· [Documentation](../../docs/README.md)

## Internal adapter reuse

`_verify_request` and `_authenticated_transaction` are trusted Python adapter
seams, not routable endpoints. The latter resolves the current identity and
executes a configured callback in the policy transaction; it never accepts a
callback from HTTP. [Artifact metadata API](../artifacts_api/README.md) reuses
this boundary without duplicating authentication or caching policy decisions.

## Unified session host

An explicit `session_boundary` constructor mode delegates authentication and every
transaction to the configured [session boundary](../session_api/README.md), never
to a synthesized bearer identity. It cannot be mixed with bearer configuration.
The [unified host](../application_host/README.md) supplies its cookie response
envelope and fixed module routing; it does not enable a real provider or change
this package's existing explicitly configured bearer API.
