# Session/policy transaction and HTTPS cookie boundary

Status: configured WSGI adapter with synthetic tests. **Real GitCode login remains
disabled by default.** No listener, deployment configuration, default identity or
production credentials are supplied. Existing bearer APIs and demos are unchanged.

## One authorization transaction

`SessionPolicyStore` opens the existing workspace policy DB and attaches the
existing login DB on the **same SQLite connection**. Both files must be on the
same device, use `DELETE` rollback journals and full synchronization. Unsupported
modes such as WAL fail closed; this adapter does not change operator settings.

Every operation takes `BEGIN IMMEDIATE` across both files, then checks current
session/CSRF, active local identity binding and workspace policy in that snapshot.
Module/resource/audit changes, session revocation checks and the final expiry
check share the commit. There is no session-to-short-lived-bearer conversion or
cached identity authority. An independent logout/revoke or policy mutation
serializes before or after the protected operation. A writer already holding the
lock may complete before logout; after logout commits, that session cannot start
a new authorized operation. This is a linearization boundary, not cancellation of
an operation already committed or a background job that already ran.

A failed/expired operation rolls back business changes in both stores while
retaining the observed login-clock high-water. Trusted module callbacks can use
the same connection for dedicated tables, but must not perform network I/O,
external side effects or nested transactions. An added commit guard runs before
the final session check, not after it.

This composition relies on SQLite's local rollback-journal/filesystem guarantees:
[ATTACH atomicity](https://www.sqlite.org/lang_attach.html) and
[multi-file commit](https://www.sqlite.org/atomiccommit.html#multi_file_commit).
It is not a distributed or network-filesystem database solution. Host crash/power
loss testing, filesystem hardening, throughput and production backup/recovery
remain unverified. Paths, schemas, bindings and callback code are trusted server
configuration, never client-selected data.

## HTTP surface

`SessionWorkspaceApplication` reuses the existing workspace resource/membership
routes through this transaction boundary. This does not automatically convert
other module APIs, administration wrappers or existing browser UIs to cookies.

- `POST /auth/start`: exact same-origin request, JSON `{}` and
  `X-MS-Collab-Login: 1`. Existing sessions also require their CSRF proof.
  Returns a fixed-provider `authorization_url` for the UI to navigate, with
  high-entropy state and S256 challenge; never the verifier. Sets a fresh
  pre-login browser cookie. It does not use a cross-origin fetch redirect.
- `GET /auth/callback`: exact configured callback, strict bounded query parsing,
  no duplicate or unknown parameters, browser/state binding and one-use exchange.
  On success rotates session/CSRF cookies, clears the pre-login cookie, and uses
  a fixed 303 redirect to `/`. No request-selected return URL is accepted.
- `GET /auth/session`: current active local principal ID only, under the shared
  transaction. It does not grant a role or enroll an unknown upstream account.
- `POST /auth/logout`: exact Origin, JSON `{}` and valid session/CSRF pair; revokes
  that session and clears all three cookies. GET is not a logout operation.

A strictly same-origin start/logout request carrying a revoked, expired or invalid
old session receives 401 with clearing cookies; the user must explicitly retry
start. A valid session with missing/wrong CSRF keeps its cookies, as do dependency
failures (503) and cross-origin requests. This permits recovery without treating
an unavailable session store as anonymous authentication.

All routes require a trusted `wsgi.url_scheme=https` and exact configured Host.
Forwarded headers never override them. Authorization headers are rejected, not
mixed with cookie credentials. The fronting HTTP server must reject duplicate raw
headers and supply normalized trusted HTTPS/Host data; WSGI cannot recover raw
headers that a proxy already collapsed.

Protected mutations require the exact Origin plus `X-CSRF-Token`, the matching
CSRF cookie and the server-stored hash bound to that session. SameSite alone is
not treated as CSRF protection. Cross-site/same-site Fetch Metadata is rejected
on protected routes, with a callback-specific navigation exception. Unknown
session tokens and malformed, duplicate or oversized cookies fail closed;
unrelated, well-formed cookies are allowed. No CORS policy is enabled.

## Cookies and response controls

- `__Host-ms-collab-session`: Secure, HttpOnly, SameSite=Lax, Path=/, no Domain.
- `__Host-ms-collab-login`: same flags, short one-use transaction lifetime.
- `__Host-ms-collab-csrf`: Secure, SameSite=Strict, Path=/, no Domain; intentionally
  **not HttpOnly** so same-origin JavaScript can send it in the CSRF header.
  It is not a session credential and cannot replace one.

Cookie lifetime cannot exceed server-side expiry. Responses are no-store,
no-referrer, nosniff and non-frameable; session tokens never appear in JSON or
redirect URLs. The authorization URL contains OAuth state and must not be logged.
Applications still need XSS prevention, TLS termination, rate/concurrency limits,
secure operational logging and browser lifecycle handling.

These boundaries follow the browser vendor's
[Set-Cookie guidance](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Set-Cookie)
and [OWASP CSRF guidance](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html).

## Provider gate remains closed

A separately reviewed bound-code adapter and fixed authorization endpoint/scopes
must be explicitly supplied together. Without one, start/callback return 503 and
do not mint an attempt/session. The existing GitCode wire client is not such an
adapter. No boolean enables unverified PKCE support. The
[GitCode protocol evidence gap](../gitcode_identity/README.md) and
[bound-code verification contract](../login_state/README.md) still apply.

Live provider PKCE/code-injection/downgrade and upstream revocation behavior have
not been verified. A local session alone cannot establish those provider facts.
Subject-wide revocation still deletes existing sessions rather than disabling an
account; in-flight/new login may establish another session, but each protected
operation rechecks the current active local principal and workspace membership.

## Verification

```bash
python3 -m unittest discover -s packages/session_api/tests -v
node packages/session_api/tests/browser-check.cjs
```

The browser command requires the repository's locked Playwright tools, Chromium
and OpenSSL. [Test fixtures](tests/README.md) use only temporary synthetic stores,
a test-only code verifier and an isolated local certificate. No private runtime
files, raw logs or real credentials belong in this repository.

## Configured module composition

The [unified service host](../application_host/README.md) now composes selected
module dispatchers through this same cookie response envelope and transaction
boundary. Existing standalone bearer APIs/UIs are not implicitly converted.

## Principal generation binding

Composition now binds the login-store object before any login begins. Session
issuance and protected requests compare realm + principal ID + generation and
current issuer/subject; schema v1 and legacy unbound authority fail closed in the
composed host. There is no automatic adoption or database upgrade. Refer to the
[authority foundation](../../docs/principal-authority.md), including code-level
closed identity administration and the remaining local-ID ACL migration gates.
