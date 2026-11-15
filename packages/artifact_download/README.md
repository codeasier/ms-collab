# Authorized artifact download service

A separately mounted, content-bound **server API** for small approved objects.
No permanent/presigned URL, anonymous link, client-selected origin, cloud account
setup or production migration is included. The legacy catalog remains metadata-only. A separately opted-in unified session
host can mount the [confirmed-byte browser interaction](SESSION-DESIGN.md);
metadata access never enables it or grants byte access.

- [Policy and persistent leases](records.py)
- [Fixed OBS GetObject transport](provider.py)
- [Acquisition, integrity and output lifecycle](service.py)
- [Legacy same-origin bearer HTTP adapter](application.py)
- [Cookie-session adapter and trusted configuration](session.py)
- [Session integration design](SESSION-DESIGN.md)
- [Synthetic verification](tests/README.md)
- [Migration and remaining work](MIGRATION.md)

## Explicit byte authority, not metadata permission

The catalog stores key/size/time, which cannot prove an object's byte identity.
A trusted host must therefore provision a `DownloadPolicy` in the **same** SQLite
policy database with an explicit downloader set and `ContentBinding` for each
object: exact literal key, size, whole-second zoned modified time, quoted strong
ETag, and independently approved SHA-256. These are not client inputs or inferred
from an untrusted listing. They must be reviewed before initialization; this
package provides no HTTP grant or hash-approval endpoint. A changed existing
policy is rejected by additive/idempotent `initialize_downloads`; rotation is an
explicit host migration that must invalidate old active work.

Every download requires all of: current verified principal binding, an active
(non-archived) workspace, active workspace membership, enabled artifacts.read, explicit index-reader grant,
explicit downloader grant, exact approved published object and metadata match,
and the requested published revision. Private candidates, directory markers,
unbound objects, other connections/buckets and public-space fallback are denied.
Being an administrator, operator or metadata reader is not a download grant.
The underlying [workspace authorization](../workspaces/README.md) and
[approved index](../artifact_sync/README.md) are prerequisites, not replacements.

## Trusted composition and HTTP contract

Construct `ArtifactDownloader(service=configured_ArtifactSynchronizer)` and
`ArtifactDownloadAPI(downloader=downloader, origin=trusted_https_origin)`.
The synchronizer supplies its existing reviewed verifier, current policy store,
fixed provider selection and scope/origin-bound secret loader. No default live
verifier, listener, environment credential discovery or insecure TLS exists.
Numeric `127.0.0.1` HTTP is accepted only for isolated tests.

`POST /api/workspaces/{workspace}/artifact-download/{connection}/{bucket}/download`
requires opaque `Authorization: Bearer …`, `Content-Type: application/json`, a
bounded Content-Length, quoted `If-Match: "published_revision"`, and exactly
`{"key":"literal/storage/key"}`. Obtain the published catalog revision through
the existing reader API. No download URLs or availability claims are exposed by
the catalog. Range/If-Range, transfer-encoding, query tokens, extra fields, GET,
cookies as identity, redirects and cross-origin requests are unsupported.

A successful response is `application/octet-stream` with an ASCII-safe attachment
filename, exact Content-Length, X-Artifact-SHA256, no-store and nosniff. It never
forwards upstream content type, disposition, cloud metadata, credentials or URL.
Before binary headers, failures use the existing value-free JSON error shape.
After streaming begins, failures abort the iterable with `download_interrupted`,
never append JSON or report a truncated body as success. Consumers must check
transport completion, declared size and checksum; a partial file is not valid.

## Explicit cookie/session integration

The unified host requires both `HostConfig.artifact_downloads=True` and an
`ArtifactDownloadConfiguration` with an explicit scoped credential loader. It
constructs the downloader on its exact current session/policy boundary; it cannot
accept a preauthenticated foreign downloader. Omitted configuration mounts no
route or button. Index refresh remains separately disabled.

Both session routes are exact, same-origin POST with current cookie + CSRF,
`If-Match` and bounded JSON. `/availability` accepts only `{"key":"..."}` and
returns the authorized scope/key/size/SHA-256/published revision without a lease,
secret lookup or provider request. `/download` additionally requires
`expected_sha256`: admission compares this constraint to the current trusted
binding in the same transaction **before** any lease, credential or upstream
operation. A changed digest/publication yields 412, not an automatic retry.
Possessing a hash is not a grant. Neither route accepts bearer identity or a URL.

The original session request is revalidated at every acquisition/output guard,
not converted to a cached principal or bearer. Session errors use Cookie/Origin
cache variation, never a Bearer challenge. Cleanup uses only the exact policy
store's admitted lease, even after logout. It cannot revive session authority.

The browser first presents exact scope, literal key, size and SHA-256, then needs
an explicit confirmation. It bounds input to 5 MiB and 30 seconds across fetch,
reads and digest, verifies headers/length/full hash before creating an ephemeral
Blob URL, and clears/cancels on scope, session, pagehide or Cancel. Delayed responses
are fenced even when abort is ignored. No permanent URL, persistent client cache,
automatic retry or server filename/path forwarding exists. A successful click
means only **handed to the browser**, not proof a file was saved. Already delivered
bytes cannot be recalled. Synthetic HTTPS tests are not real cloud acceptance.

## Integrity, limits and concurrent revocation

- Fixed virtual-host OBS HTTPS endpoint from trusted bucket/region, public-only
  DNS validation and pinned socket with original-host TLS verification. Literal
  keys are encoded once, not treated as URLs/query parameters. Requests use OBS
  header authentication and If-Match. No redirects, proxy environment, ranges,
  decompression, retries, versionId or customer-provided encryption keys.
- Only 200 with exact, unique length/ETag/Last-Modified and identity encoding is
  accepted. The transport does not emit error response bodies to the client.
- At most **5 MiB** per explicitly bound object. Upstream is consumed in at most
  **64 KiB** chunks into an anonymous 0600 temporary file. Its complete SHA-256 is
  verified **before any object bytes or 200 headers** can be emitted. No public
  file path or reusable response cache exists; cleanup closes the file.
- A durable write-transaction lease admits at most **2 active downloads per
  policy database**, at most **1 per principal**, including other service
  instances. The lease is never a bearer token. Audit stores scope, actor, key
  digest, published revision, size and bytes **offered** to the WSGI server, not
  a claim that the client received them. No raw filenames or credential values.
- Authority expires after **30 seconds**, checked by wall clock and monotonic
  elapsed time. Before and after upstream reads, and before every downstream
  chunk, current identity, membership, module/grants, published bytes metadata,
  policy bindings and lease are rechecked in one authorization snapshot. A new
  private candidate alone does not revoke an unchanged approved publication.
- Current revocation, assertion expiry, new publication or binding change stops
  remaining output. Already yielded/buffered bytes and a chunk admitted just
  before a concurrent commit cannot be retracted. This is not zero-byte atomic
  revocation, nor deletion of files a user already downloaded.

### Host responsibilities and fail-closed recovery

The transport has a whole-network timer that shuts down an established socket,
short socket timeouts and `read1` to avoid unbounded fill-to-64-KiB slow reads.
A DNS/TLS completion after deadline cannot send the signed HTTP request. Python
cannot forcibly cancel synchronous system DNS, an arbitrary trusted credential
loader or a blocked WSGI/proxy/client write. Deployments must additionally enforce
resolver, secret-provider, worker and downstream total deadlines and bounded
request/header reads. Do not advertise this library alone as a hard wall-clock
process cancellation guarantee. Keep response buffering/caching disabled and
ensure the WSGI server calls iterable.close on disconnect/error.

**Expiry does not free capacity while a worker might still own a resource.**
Expired active leases and cleanup/audit failures remain reservations until the
owning request closes successfully. After a process crash or cleanup failure,
the trusted host must confirm its socket/tempfile/worker are gone before an
explicit audited recovery marks the reservation interrupted. There is no public
cleanup or auto-expiry bypass endpoint. Until recovery, capacity fails closed.
Retention of completed audit records and protected database/temp storage are
host operations; this package does not delete history or expose filesystem paths.

## Protocol evidence

Read 2026-11-15: Huawei [GetObject](https://support.huaweicloud.com/intl/en-us/api-obs/obs_04_0083.html)
and [header authentication](https://support.huaweicloud.com/intl/en-us/api-obs/obs_04_0010.html),
plus the vendor Go SDK's [encoded key/canonical URL construction](https://github.com/huaweicloud/huaweicloud-sdk-go-obs/blob/master/obs/conf.go).
This is a conservative protocol subset, **not live cloud compatibility proof**.
Synthetic signature, response, DNS, authorization and real loopback HTTP tests do
not validate a real bucket, actual identity provider, deployment proxy or large
object performance. See [remaining work](MIGRATION.md#remaining-work).

In-progress byte authorization pins now include the policy realm and actor
generation. This detects synthetic identity ABA before subsequent byte release.
Persisted downloader/index local-ID grants are **not** migrated by this change;
identity administration remains code-level closed. See the [consumer inventory](../../docs/principal-authority.md).
