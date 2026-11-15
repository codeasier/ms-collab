# Login transaction and session core

Status: offline-tested server primitives, **not enabled GitCode login**. No HTTP
routes, cookies, production startup, user provisioning or network provider ships
in this package. It does not change the public demo or protected workspace API.

## Implemented boundary

- An explicitly initialized, private local SQLite store with an exact trusted
  issuer/client/callback/configuration binding and versioned schema. Reopening a
  missing, unrelated or differently configured store fails closed.
- Independent 256-bit state and browser-binding values per attempt; a fresh
  high-entropy verifier and RFC 7636 S256 challenge. Only state, browser binding,
  challenge and expiry leave `begin`; the verifier stays server-side.
- Exact callback matching, duplicate/unknown parameter rejection, one-use state,
  browser binding, bounded expiry and cross-process atomic consumption **before**
  provider exchange. Valid provider errors and failed exchanges cannot replay.
  Invalid browser bindings cannot burn another browser's legitimate attempt.
- Fresh application-bound session/CSRF token pairs after trusted bound-code
  verification. Runtime storage holds hashes of state/browser/session/CSRF tokens,
  not raw tokens. Session expiry is capped by both configured lifetime and the
  verified upstream assertion. Reads never silently extend it.
- Explicit previous-session rotation, CSRF-paired logout, trusted subject-wide
  revocation and fresh reads across processes. No refresh token or user profile
  is stored. Capacity limits and expired-record cleanup bound row growth.
- Generic authentication errors, database failures and clock-rollback denial.
  Expiry is rechecked before returning an attempt or session snapshot.

A session snapshot is **not workspace authority**. Membership, account activity,
module state and object scope must still be evaluated against current policy.

## Why GitCode remains closed

`LoginCoordinator` without a bound-code exchange adapter returns 503 before
creating an attempt. The existing [GitCode wire client](../gitcode_identity/README.md)
intentionally cannot satisfy that adapter interface. There is no
`pkce_supported=True` switch or fallback to state-only login.

The trusted `BoundCodeExchange` contract requires a real adapter to validate
issuer, client, exact callback, code lifetime/replay and S256 binding, including
rejection of a wrong/missing verifier or a code issued without a challenge. A
returned `VerifiedIdentity` is trusted server output, never HTTP input. Merely
writing a class with this method, or sending an optional verifier that a provider
ignores, is not evidence of secure provider behavior. Test doubles exist only in
[tests](tests/README.md), and are not application configuration examples.

Official evidence reviewed 2026-11-15:

- [GitCode OAuth guide](https://docs.gitcode.com/en/docs/apis/oauth/) documents
  state and code exchange, but the inspected material does not establish PKCE
  parameter handling, downgrade rejection, OIDC nonce or token audience claims.
  This is an evidence gap, not a claim that the provider lacks those features.
- [RFC 9700](https://www.rfc-editor.org/rfc/rfc9700.html#section-4.5.3) distinguishes
  authorization-code injection from CSRF; the code must be bound to its client
  instance. Its PKCE downgrade guidance requires rejection of inconsistent flows.
- [RFC 7636](https://www.rfc-editor.org/rfc/rfc7636.html#section-4) defines the S256
  material used by this core. Computing it locally does not verify provider support.

Before a GitCode adapter can be enabled, obtain authoritative protocol evidence
and use an isolated non-production application to verify correct, wrong, missing,
replayed and unbound-code cases. That live test needs separate authorized app
configuration and credentials. No such credentials or requests were used here.
Do not substitute arbitrary PAT-to-user lookup for application-bound login.

## HTTP and policy integration still required

A future HTTP adapter must enforce trusted HTTPS origin/callback configuration,
strict raw query parsing with duplicate preservation, no credential logging or
referrer leakage, rate/concurrency/body limits, and safe fixed redirects. It must
set fresh host-only `__Host-` Secure/HttpOnly/SameSite cookies with `Path=/`, no
Domain attribute, and correct expiry. The browser-binding cookie must be rotated
for each attempt; a second tab starting login invalidates the first tab's cookie
binding rather than mixing their transactions.

`begin` does not itself implement a login-start CSRF/Origin check. Cookie-authenticated
mutations need method/Origin/CSRF validation, not just session existence. `check_csrf`
checks a token pair only, and does not authorize a later mutation. Logout must not
be exposed as a GET. `revoke_subject` is trusted administration, not a public route.

This standalone package does not coordinate workspace commits. Do **not** cache
its snapshot as a verifier assertion. The optional
[session/policy HTTP composition](../session_api/README.md) adds a shared local
SQLite transaction for current session, identity and policy checks; its strict
rollback-journal/storage limits must be met. Other callers still need explicit
coordination. This package provides no `Provider.verify` implementation that
would obscure that boundary.
Subject-wide revocation deletes existing sessions only. It is not an account
disable or login-generation barrier: a verified login already in flight, or a
new login, can create another session. Local identity binding/enrollment, current
account activity checks and upstream revocation handling are also still required. An upstream logout is not automatically known to this local store.

## Sensitive runtime storage

Initialization allocates a new file with mode 0600 and refuses replacement. The
store is not a filesystem access-control boundary: directory permissions, backups,
journals, ownership and secure local deployment remain operator responsibilities.
The short-lived PKCE verifier is **plaintext** in pending rows. Deletion does not
guarantee erasure from SQLite pages, journals, backups or process memory. Never
commit, upload, log or reuse runtime DB files as fixtures. Hidden `repr` fields do
not protect against debugger/exception-local dumps. Observed clock high-water is retained even when business changes are rolled
back on denial, so an observed expiry cannot be undone by a later clock rollback.
Clock rollback fails closed and needs operator diagnosis, not database editing to bypass the check.

## Verification

```bash
python3 -m unittest discover -s packages/login_state/tests -v
```

Tests are synthetic and offline, including the authorization-server double,
code-injection/downgrade failures and multi-process one-use consumption. They are
not live provider, browser-cookie, TLS, deployment or cross-store revocation
acceptance. See the [identity boundary](../identity/README.md).
