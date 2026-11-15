# Authorized community UI

Independent static wrapper for an explicitly configured `CommunityApplication`.
It does not replace the legacy synthetic demo, provide an identity provider,
start a default listener, or enable production deployment.

```python
from packages.community_ui import CommunityUI

# community_api is a trusted composition of store, verifier, provider,
# credential loader and operator-configured per-connection sharing policy.
application = CommunityUI(api=community_api, origin="https://collab.example.invalid")
```

Serve `/community/` and `/api/` at that exact origin. Only the four allowlisted
static files are exposed. The wrapper rejects Host, scheme and Origin mismatch
and cross-site requests. HTTPS is required; numeric IPv4 loopback HTTP is allowed
only for local acceptance. Preserve the API's no-store responses and the
wrapper's CSP, nosniff and no-referrer headers. No CORS or cookie authentication
is enabled. A trusted TLS front end must supply the actual configured WSGI
scheme/Host and enforce request/body limits. Do not trust client forwarded headers.

## Sharing workflow

- Supply an opaque access credential for the configured verifier, not a default
  or bundled GitCode credential. It is held in page memory only, cleared from the
  input immediately, and never put in storage, URLs, logs or provider links.
- Enter the exact operator-provided workspace, connection and repository scope.
  No discover-all endpoint or union of private workspaces is used.
- The current `/capabilities` response determines which views/actions appear.
  The server reauthorizes every request. A workspace administrator alone is not
  assumed to be a reader, approver or refresher.
- Shared items contain only explicit exact-content approvals. Review candidates
  remain private to authorized reviewers. Refresh does **not** approve content.
- The UI fully loads the chosen view in pages of 100 (maximum 10,000 rows) and
  requires one consistent scope, revision, total and observation across pages.
  An interrupted, mixed, malformed or inconsistent response clears the panel.
- Review editing starts only after the complete snapshot loads. Pagination and
  filtering change display, not the approval set. Saving explicitly confirms the
  precise scope/version, additions/removals and total approvals (maximum 200).
  `/approve` replaces the entire set. There is no default select-all action.
- Every write sends `If-Match`. Conflicts discard stale choices and re-read
  current permissions/content; writes are never automatically retried. An
  uncertain result tells the operator to verify refreshed state.
- Disconnect and page lifecycle exit abort pending requests and clear credential,
  scope, private rows and dialogs. Already accepted writes may have completed.
  Disconnect is not issuer-side revocation. Requests from an older scope cannot
  overwrite a newer view.

Titles/labels are untrusted text rendered with `textContent`, never HTML. No
provider URL or external image is rendered, and CSP limits scripts/styles/API
requests to the same origin. These boundaries are not a complete deployment
security review. API/store authorization remains the security authority.

## Synthetic checks

`community-ui.yml` runs Python wrapper tests, Node client contract tests and the
Playwright flow. The fixture is loopback-only and uses a temporary synthetic
store/verifier/provider; it never loads external credentials or provider data.
The CI uploads only named synthetic desktop/mobile screenshots for three days.
See [frontend tests](../../frontend/community/tests/README.md).
