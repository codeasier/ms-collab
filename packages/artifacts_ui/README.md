# Authorized artifact browser

A read-only user interface for the configured [artifact metadata API](../artifacts_api/README.md),
not a login flow, real storage provider, downloader or production deployment.
The original synthetic demo remains unchanged. This page is a separate integration
at `/artifacts/` and contains no default credential, data or listener.

## Trusted composition

```python
from packages.artifacts_ui import ArtifactBrowserUI
application = ArtifactBrowserUI(
    api=configured_artifact_api,
    origin="https://collab.example.invalid",
)
```

The factory supplies an initialized `ArtifactApplication` and one fixed trusted
origin. Host, scheme and Origin must match; cross-site browser requests are
rejected. Non-TLS origins are accepted only for numeric loopback with a port in
isolated tests. Proxies must normalize these fields safely, not trust arbitrary
forwarded headers. Only four explicit static asset paths are served; there is no
filesystem traversal or generic file-serving endpoint. The strict CSP disallows
inline script, external connections, framing, forms to other destinations and
base-URL changes. Cookie authentication and CORS are not added.

The server-side verifier and same-snapshot workspace/module policy remain the
only authorization authority. Browser controls and response validation are
additional UX/defense, not access grants. Each API request is bearer-only and
same-origin, omits cookies, rejects redirects and uses `no-store`. The credential
stays in module memory and is never put into a URL or browser storage; its input
is cleared on connect. Disconnect, authentication failure and `pagehide` abort
pending reads and clear catalog, scope and credential state. This is not upstream
revocation or secure memory erasure.

## User workflow

1. Connect with an opaque credential accepted by the configured service.
2. Enter an exact workspace, connection and bucket ID provided by an administrator.
   This slice does not discover providers/buckets or imply access from a typed ID.
3. Select an observed quarter, open folders, move up, filter literal case-sensitive
   key text, sort, include subfolders/files only, or paginate the snapshot.
4. Optionally open an exact named-view ID. A missing view fails with no broad-query
   fallback. View mode disables folder navigation and manual definition controls;
   returning to ordinary browsing is an explicit action.

Every displayed result validates the exact scope, quarter and pagination, entry
shape and catalog/view revision. Old responses cannot overwrite a newly selected
scope. Scope and filter changes clear stale rows immediately. Untrusted names
and keys render only as text, never HTML or executable URLs. Files are not links
and there is no download action. A shared space shows only its own authorized
catalog; it is never a union of private spaces. Permission remains module-wide
within the workspace, not a new per-bucket ACL.

Observed timestamps and revision numbers identify stored evidence. Neither a
catalog nor its UI proves a current external bucket inventory, a successful sync,
or access to a remote provider. The underlying bounded metadata limits still
apply. Empty, missing-quarter, failed and inaccessible states are not conflated.

## Validation and remaining work

```sh
python3 -m unittest discover -s packages/artifacts_ui/tests -v
node --test frontend/artifacts/tests/*.test.mjs
```

[Hosted acceptance](../../.github/workflows/artifact-ui.yml) launches a temporary
SQLite store and test-only verifier. Its real browser-to-WSGI exercise covers
directory/quarter navigation, filters/sort/pagination, named-view failure without
fallback, cross-space denial, malicious text, mixed-scope payload rejection,
stale responses, 401 clearing and pagehide cleanup. Only two synthetic desktop
and narrow-layout screenshots are uploaded for three days. Nothing uses a live
storage service or real credentials. See [test navigation](tests/README.md).

Before production exposure, the configured verifier, enrollment, TLS/proxy/header
rules, server/time/concurrency limits, credential handling, ingestion, storage
scale and operational acceptance still need their own reviewed setup. Real sync,
download authorization and stream handling, catalog management, bucket discovery
and saved-view creation/editing remain unimplemented. Do not use the test harness
as deployment configuration or attach real data to the original demo.

[Frontend](../../frontend/artifacts/README.md) · [Documentation](../../docs/README.md)
