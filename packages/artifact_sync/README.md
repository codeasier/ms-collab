# Scoped artifact acquisition and approved index

A bounded, GET-only Huawei OBS **ListObjects** implementation, a dedicated SQLite
metadata index, and a read-only API compatible with the existing
[artifact browser](../artifacts_ui/README.md). This slice has synthetic protocol,
authorization and browser tests. **No real cloud account, production dataset or
live provider compatibility has been verified. Downloads remain unimplemented.**

## Trusted integration, not an anonymous sync endpoint

1. Provision the existing SQLite workspace policy, reviewed identity verifier,
   verified principals, memberships and `artifacts` module. There is no default
   identity, automatic enrollment or credential discovery.
2. Build a `Connection` for one exact
   `Scope(workspace_id, connection_id, bucket_id)`, a remote bucket/region, an
   optional directory-boundary prefix and explicit scan limits.
3. Build `IndexPolicy(connection, credential_ref, readers, refreshers, approvers)`
   with explicit **local verified principal IDs**, then call
   `initialize_indexes(store, (policy,))` from trusted host setup. It creates two
   additive versioned tables in the **same authorization database**, never a
   second authorization cache. Identical setup repeats are safe; replacing an
   existing policy is rejected. Grant/binding changes need an explicit trusted
   migration and invalidation policy, not generic resource editing.
4. Construct `ArtifactSynchronizer` with that store, trusted identity providers,
   one fixed provider key and a `credential_loader(ref, scope, origin)` supplied
   by an approved secret provider. It must return a `BoundCredential` matching
   that exact scope and computed origin, with a dedicated least-privilege cloud
   AK/SK and optional session token. Secrets are neither stored in the index nor
   taken from environment variables, URLs, browser input or request bodies.
5. Call `refresh(user_credential, scope, expected_revision=...)` from trusted host
   code with the user's opaque `Credential`, not a self-reported actor ID.
   Refresh creates a **private candidate**, not reader-visible data.
6. An explicitly granted current workspace admin calls `review(...)` to inspect
   the complete candidate, its digest and exact reader IDs. After a real sharing
   decision, call `approve(..., candidate_digest=..., expected_revision=...)`.
   Never automatically turn a successful refresh into approval. The approver
   need not be the refresher; this API does not enforce distinct humans.
7. Mount `IndexedArtifactApplication` instead of the old resource-backed
   `ArtifactApplication` for this catalog, optionally wrapped by
   `ArtifactBrowserUI(api=..., origin=...)`. Routes, filters, named views and
   missing-scope behavior follow the [artifact API](../artifacts_api/README.md).
   Only approved snapshots reach readers. No refresh/approve/download HTTP route
   or scheduler is supplied by this package.

A shared/public workspace still requires explicit membership **and** the stored
reader grant. Token-visible bucket metadata is not automatically public. The
scope is workspace + connection + bucket, not an ACL inferred from a filename or
named filter. Do not mount a parallel broadly shared legacy catalog with the
same private content. The host is responsible for a usable secret manager,
current verified identities, sharing decisions, lifecycle/config migrations,
TLS, request deadlines, monitoring and backups. Avoid logging credential objects,
request headers, local variables or raw provider responses/tracebacks.

## Transaction and failure contract

- Refresh requires a current active workspace admin, enabled `artifacts.write`,
  and the exact refresher grant. Review requires an active admin, read capability
  and approver grant; approval requires write capability and approver grant.
  Read access separately requires current membership, module read capability and
  reader grant. Platform-operator status does not bypass these grants.
- Verify the user's assertion before I/O, then resolve its current subject
  binding in every policy snapshot. Immediately before **each** external GET,
  recheck membership, module state, role, grants, policy bytes and the original
  revision. Network work runs outside database locks. A revocation can commit
  after this check but before or during actual dispatch; already sent bytes
  cannot be retracted. External I/O and local authorization are not atomic.
- In the final `BEGIN IMMEDIATE`, recheck the same authority and pinned actor,
  policy and revision; write candidate and its audit event in the same transaction.
  Assertion expiry after writing or before commit rolls both back. Approval uses
  current authority, exact revision and exact whole-candidate SHA-256 digest.
- A published index has its own stable revision. Subsequent refreshes remain
  private pending candidates; readers do not see their revision, contents or
  pending status. Readers load current grants and published metadata in one
  SQLite snapshot. A concurrently committed revoke applies to the next request,
  not retroactively to an already admitted snapshot.
- There is no fallback across workspace, connection or bucket. Malformed schema,
  embedded scope, timestamp or index records fail closed with redacted HTTP errors.
  Provider errors have stable value-free codes. Failed/partial scans, concurrent
  updates, authorization failures and audit failures preserve the prior index.

## Provider and index limits

Only standard regional virtual-host HTTPS
`{bucket}.obs.{region}.myhuaweicloud.com` is supported. Bucket labels exclude dots;
there are no custom endpoints, private endpoints, proxy settings, redirects,
user-supplied URLs, object reads or signed download URLs. The transport resolves
once, rejects any non-public DNS answer, pins a public address and preserves TLS
hostname/certificate validation. Authentication uses the provider's documented
header-signature algorithm, not URL credentials.

The parser requires the expected XML namespace, bucket, prefix, marker, page
size, URL encoding and strictly advancing keys/continuation marker. It rejects
DTD/entity declarations, malformed URL encoding and ambiguous duplicate fields.
Only literal key, explicitly zoned modification time, integer size and directory
marker survive parsing; owner data, ETags and unrelated response fields do not.

Bounds: 1–100 objects/page, 1–10 pages, at most **500 scanned objects**; 1–720 hour
recent window; 1–15 second socket timeout; at most 512 KiB per XML response.
Configured page size × page count cannot exceed 500. The service scans the whole
configured prefix within these limits because ListObjects has no modification-time
filter. Hitting a limit while truncated is a failure, never a partial success.
A socket timeout is not a total job deadline; the host must enforce that deadline.

The window subset is merged by literal key into the previous candidate or approved
index. Unseen objects are retained, **not deleted**. Older metadata, changed data
at the same timestamp, future timestamps and equal/older observations fail.
Checksum files are retained. This is neither complete history nor a bucket-wide
point-in-time snapshot; concurrent remote mutation cannot be fully detected from
paginated listing. No object-version enumeration or delete reconciliation exists.
The cumulative index is capped at **500 objects and 1 MiB**, with keys limited to
1024 UTF-8 bytes and 32 path segments. Over-limit merges fail without silent
pruning. This bounded implementation is not yet a large-scale artifact service.
Quarter offset is explicit; no deployment-local timezone is guessed.

The API identifies its source as `stored_index`, `full_history_complete: false`,
`live_provider_verified: false`, `download_available: false`. The verification
flag describes this package's compatibility evidence, not a claim that a stored
snapshot was fetched live during the reader's request.

## Validation and provenance

```bash
python3 -m unittest discover -s packages/artifact_sync/tests -v
# With the repository's pinned Playwright tooling:
node packages/artifact_sync/tests/browser.cjs
```

Synthetic tests cover wire signatures, strict pagination/XML, fixed-host TLS/DNS,
current authorization and cross-scope failures, private candidates/approval,
revocation between pages, CAS races, expiry/audit rollback, schema corruption,
incremental retention and cumulative bounds. A real loopback HTTP test and a
hosted browser fixture exercise the SQLite index through the existing UI without
real cloud calls; screenshots contain only synthetic approved names. See
[tests](tests/README.md), [migration provenance](MIGRATION.md) and
[documentation index](../../docs/README.md).

## Optional management surface

The separate [artifact administration API and browser](../artifact_admin/README.md)
now expose configured refresh and complete-candidate approval. They require the
same trusted synchronizer and add mandatory exact-reader comparison to HTTP
approval. The original package remains a trusted service, not a default listener.
Protected downloads and live cloud compatibility acceptance are still separate.
