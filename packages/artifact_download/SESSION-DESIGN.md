# Session download integration contract

This design composes the reviewed bounded byte service into the unified product
entry. It does not configure a real cloud account, identity provider or deployment.

## Server authority and composition

A disabled-by-default host switch requires the artifacts module and an explicit
trusted download configuration. The configuration supplies a scoped credential
loader and, optionally, a trusted transport. There is no environment discovery,
HTTP URL/token/actor input or implicit fixture. The host constructs the download
synchronizer around its exact existing session boundary; it never accepts an
externally authenticated downloader or converts a cookie into a bearer.

Only exact POST availability/download routes are accepted, with current CSRF and
quoted published-revision If-Match. Availability JSON contains only the literal object key; download additionally
requires the checked `expected_sha256` as a precondition, never a grant. Availability is a current authorization transaction, not a grant or
reservation: it returns only that authorized object's scope, key, size, SHA-256
and published revision. It does not load secrets or access a provider. Download rechecks the same byte authority and all existing scope/publication fences.
Its admission transaction compares the confirmed digest against the current trusted
binding before reserving a lease, loading a secret or contacting the provider. A
changed digest returns 412 even if key/size/time and published revision are unchanged.
The legacy bearer contract remains unchanged.

The session request remains intact across every acquisition and output check.
Each check resolves session, CSRF, identity and policy in the same transaction.
Cleanup is deliberately different: the downloader binds the policy store from
its own configured authority once, then may finish only its already admitted
lease, including after logout. Cleanup cannot change a grant or select an actor.

The binary adapter is separate from the legacy bearer WSGI entry. JSON failures
use the session error envelope, without a bearer challenge. Binary responses use
Cookie/Origin variation, no-store/no-cache, nosniff, no-referrer and attachment
sandbox headers. Once output begins, failure interrupts the stream; it never
appends a JSON error or redirects. Range, If-Range, transfer encoding, URL query,
GET and mixed bearer authentication are rejected.

## Browser authority and byte lifecycle

A configured feature indicator is process metadata, never user permission. A
user selects a file from an already published catalog and explicitly checks its
byte availability. The UI shows the exact scope/key/size/digest/revision before a
separate download confirmation. A stale check is not permission to send bytes.

The cookie client shares the existing session epoch and current CSRF cookie. It
streams at most 5 MiB under a total client deadline, verifies exact response
length/type/safe attachment name/hash header against the checked binding, and
computes the complete SHA-256 before saving. It must not call an unbounded
response.blob(), accept server URLs, follow redirects or automatically retry.

Scope/filter changes, logout, pagehide and cancellation invalidate both module
and session guards where applicable, cancel an active reader and clear buffers.
Every asynchronous read and digest completion, and local Blob URL creation/click,
requires the current guards. An ephemeral local Blob URL is created only after
all checks, is never displayed/shared/persisted, and is promptly revoked. The UI
must not report a native file-save completion merely because an anchor clicked.

## Limits and non-guarantees

Existing server limits remain 5 MiB, 30 seconds, two active leases per policy
database and one per principal. Expiry or failed cleanup never silently releases
possibly held resources. Host recovery, DNS/secret-provider/process/downstream
hard deadlines and safe local storage remain deployment responsibilities.

An approval/revocation committed just after a check cannot retract an admitted
chunk, buffered bytes, or a file the browser has saved. Cancellation does not
promise rollback or deletion of such bytes. Browser checksum validation is an
additional integrity check, not a substitute for server authorization. All
acceptance uses synthetic HTTPS fixtures and does not prove real cloud, identity
provider, production proxy or large-object compatibility.

Return to [download service](README.md).
