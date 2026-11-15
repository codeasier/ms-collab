# GitCode community snapshot adapter

A standard-library-only migration slice: explicit GitCode issue/PR reads →
allowlisted `community_board.Snapshot` → atomic SQLite publication. There are
**no live provider calls in tests**, no credential discovery, no scheduler, no
production default repository, and no upstream mutation methods.

## Trusted application integration

```python
from packages.community_board.model import Scope
from packages.community_sync import BoundCredential, Connection, SnapshotStore, refresh

# Trusted operator/application code, NOT values accepted directly from an HTTP
# request. First authenticate the principal and authorize the workspace board
# capability, repository connection, and refresh operation.
scope = Scope("example-team", "example-connection", "example-owner/example-repo")
connection = Connection(scope, page_size=100, max_pages=10)
# token is supplied by an approved secret provider, never a source-code literal.
credential = BoundCredential(scope, token)
store = SnapshotStore(operator_selected_private_database_path)
# Call store.initialize() once during explicit setup; existing files are refused.
result = refresh(connection, credential, store, observed_at=authorized_job_cutoff)
# An authorized reader can load(scope) and use result.snapshot with BoardProjection.
```

The example deliberately leaves the token, private path and aware ISO-8601
cut-off to the hosting application. Nothing reads environment variables, a CLI
login, home-directory credentials, or production configuration. Use a dedicated
least-privilege GitCode token with only the required repository read access.
`BoundCredential` binds it to an exact workspace/connection/repository triple and
redacts its representation; this is routing protection, **not** a secret vault.
The same upstream repository in another connection or workspace is independent.
A public workspace is an ordinary explicit scope, never a union of private data.
Snapshots contain records visible to the supplied token. This adapter does not
implement per-record public-visibility filtering (for example GitCode
`visibility_reason`); a public upstream repository or a workspace named public
does **not** make its token-visible records safe to publish. Sharing requires
separate explicit data approval and a visibility policy in the host application.

The library is **not an authorization boundary**. A process able to call `load`
or read the database can access the selected scope. The host must enforce
current membership, capability and connection authorization for every read and
refresh, including revocation during network work. No HTTP route, user login,
admin UI, or integration with `workspace_api` is included in this slice. Do not
expose this library as an unauthenticated endpoint or deploy the demo with tokens.

## Network and publication contract

- The built-in transport only GETs the fixed `https://api.gitcode.com` issue/PR
  collection endpoints for the configured repository. Endpoints cannot be
  supplied by a response or operator URL. TLS verification remains enabled;
  redirects and environment proxies are disabled. Credentials are sent only in
  the Authorization header, never query strings, URLs, snapshots or diagnostics.
- Each list is ordered by update time, descending, with explicit page size and
  page bound. Responses are bounded (default 2 MiB per page), decoded strictly,
  and normalized to kind, number, title, state, update time and label names only.
  Unknown lifecycle states (including `locked`), nonnumeric IDs and incompatible
  response schemas fail closed rather than being guessed. Caller-visible errors
  do not contain response bodies. Titles and labels still require UI escaping.
- Both lists must succeed before anything is published. HTTP errors (including
  404 and rate limiting), duplicate IDs across moving pages, invalid/future
  timestamps and oversized data leave the previous snapshot intact. No retry
  is automatic. A row newer than the caller's cut-off aborts; re-observe with a
  new cut-off rather than inventing a timestamp.
- All results remain **observed subsets**, even when a page is short. GitCode's
  offset pagination is not a transactionally consistent repository snapshot.
  Missing records are unavailable, not evidence of deletion/closure. No webhook
  processing, tombstones, authoritative event ordering or full-history claim.
- SQLite has one latest snapshot per exact scope, with a 16 MiB normalized
  payload limit. Explicit initialization creates a private mode-0600 file and
  refuses existing files. A single transaction publishes both lists with an
  expected revision check. Stale snapshot/item timestamps and ambiguous changed
  fields at the same timestamp are rejected. Concurrent refreshes must re-read
  and re-observe; they never overwrite a winner. This conservative rule can need
  operator reconciliation when the provider's timestamps lack precision.
- A failed refresh does not mark the previous snapshot fresh. Readers see the
  retained observation time and revision. `compare_snapshots` can compute a
  scoped observed delta; this package does not persist a historical audit log.

## Verification and limits

```sh
python3 -m unittest discover -s packages/community_sync/tests -v
python3 -m unittest discover -s packages/community_board/tests -v
```

Tests use fabricated API responses and local temporary databases only. They
exercise scoped credentials, fixed-origin GET/header behavior, redirect refusal,
pagination, error redaction, normalization, cross-scope isolation, persistence,
rollback and revision races. Passing them is not a live GitCode acceptance test.
Authentication/header compatibility, real repository schema variants, rate limits,
and connection-level authorization in a real hosting application remain unverified.

## Navigation

- [Migration provenance and external API references](MIGRATION.md)
- [Tests](tests/README.md)
- [Existing read model](../community_board/README.md)
- [Documentation index](../../docs/README.md)

## Authorized HTTP integration

The separate [community API](../community_api/README.md) now provides same-policy-
database persistence, current-user authorization, explicit content approval and
two-phase refresh. It deliberately does not read this package’s independent
SQLite store during requests. Its integration does not add a default real login,
production deployment or automatic sharing.

The authorized integration supplies a trusted `before_read` callback to
`GitCodeProvider.observe`. It runs before and after each page GET, not only around
the entire scan. Standalone calls without it still make no workspace-authority
claim. See [community grant and I/O boundaries](../community_api/GRANTS.md).
