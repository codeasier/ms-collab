# Cookie-session artifact refresh contract

This is an opt-in vertical slice, not live cloud or production acceptance. The
existing `IndexPolicy` remains the sole scoped source/refresher/approver/reader
contract. Connection discovery is separate and must not create grants.

## Composition and authority

`HostConfig.artifact_refresh=True` requires artifact administration and an explicit
`ArtifactRefreshConfiguration` with a scoped secret loader and optional trusted
bounded transport. The host constructs the synchronizer and refresh controller
on its exact session/policy boundary. No request supplies an endpoint, credential,
actor, grant, provider type or import path. The default remains closed. A catalog
reader or workspace operator is not automatically a refresher or approver.

The existing administration controller's status/refresh/complete-review/approve
routes are reused with current cookie+CSRF, exact scope and `If-Match`. A refresh
creates a private candidate only. Readers keep the prior approved publication;
full candidate digest + exact reader set + explicit approval are still required.

## Bounded admission and current authority

Trusted initialization creates a strict refresh lease table in the same policy
DB; no request initializes or migrates schema. Admission atomically checks the
current session, principal binding, active workspace/admin/refresher permission,
index policy and revision before any credential or provider work. Capacity is two
active acquisitions per policy DB, one per actor and one per exact scope, shared
across processes. A lease pins actor/scope/revision/policy digest and expires after
30 seconds. Expiry never frees capacity while a worker may still own resources.

The original session request is retained. Secret loading, each page before and
after network I/O, and the final candidate write recheck current session and
policy. A changed revision/grant/policy or timeout prevents candidate persistence.
Saving the private candidate, audit event and completed lease share one write
transaction with a last deadline guard and the session boundary's final guard.
Failure cleanup can only finish the admitted lease through the exact bound policy
store, even after logout; it cannot grant authority. Cleanup failure leaves active
capacity reserved for explicit trusted crash recovery.

## Provider and timeout boundary

The default bounded transport reads only computed OBS bucket-root HTTPS GET,
using scope/origin-bound credentials, public-only DNS pinning and original-host
TLS. It applies the remaining budget to admission, GET and candidate-commit guards,
and starts a shutdown timer for connected I/O. This is not a guarantee that DNS,
TCP/TLS connection setup, arbitrary secret loading or storage returns within 30s.
Socket shutdown retains the original
socket even for `Connection: close`, bounded `read1`, exact/unique response framing
and explicit response/client cleanup. The abort thread is joined before cleanup
is declared successful; any shutdown/close failure reserves capacity. Redirects, compression, arbitrary URL/proxy
input, private destinations and unbounded pages are rejected. Existing strict
UTF-8 XML parsing, 500-object/max-page limits and incremental-window semantics
remain. No object bodies or download grants are acquired.

Transport dependencies are trusted code, never HTTP-selected. A custom transport
must honor the remaining deadline and synchronously close resources before return
or failure; an uncertain cleanup is reported separately and keeps its lease.
Python cannot force-cancel arbitrary credential loaders, DNS or host writes. Hosts
must enforce their hard deadlines and worker/storage controls; a late result
cannot publish and a still-blocked worker retains its reservation. Browser Cancel
or logout clears UI but does not claim a server-side rollback of an already
committed candidate. There is no scheduler or automatic retry. COMMIT itself is not atomic with the
wall clock; deployment must bound lock/storage waits. Concurrency limits are not
rate limits or a cloud-cost budget; hosts need separate rate/cost controls.

Trusted recovery requires proof that the old worker and all of its resources have
terminated, not merely TTL expiry, logout or an HTTP disconnect. Lease identifiers
are never reused. A late old worker cannot publish after its lease is made
inactive. No public recovery endpoint or automatic TTL reclamation is provided.

## Acceptance

Only synthetic scope-bound XML/credentials and temporary HTTPS fixtures: private
candidate isolation, explicit complete-set publication, cookie/CSRF, scope and
reader fences, concurrent admission, stale revision, logout/revoke/expiry during
acquisition, timeout and cleanup failures, late UI replies and responsive images.
A real provider/identity/deployment must be accepted separately.

Return to [package navigation](README.md).
