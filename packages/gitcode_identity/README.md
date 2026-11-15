# GitCode OAuth protocol client

Status: tested **protocol building block**, not a login implementation. No routes,
sessions, default provider or real credentials are configured. This package is
not connected to the demo or [workspace API](../workspace_api/README.md).

## What is implemented

- Explicit trusted client configuration and authorization URL formatting.
- Authorization-code exchange followed by an authenticated current-user lookup.
- Minimized `GitCodeIdentityEvidence`: stable upstream `id`, observation time and
  grant expiry only. No profile, email, login name, role, token or refresh token
  is returned or persisted. It proves no local enrollment or workspace access.
- Direct system-CA HTTPS to fixed GitCode endpoints, bounded responses and socket
  timeouts, no redirect following, environment proxies, retry or fallback.
- Strict JSON, expiry, granted-scope and subject validation; generic 401/503
  failures without upstream body or credential text.

`GitCodeOAuthClient` deliberately has no `verify` method and does not emit a
`VerifiedIdentity`. Do **not** register it as an
[identity Provider](../identity/README.md). Looking up a user with an arbitrary
GitCode bearer token does not prove the token was issued for this application.
The inspected documentation does not establish a token audience, introspection
endpoint or OIDC signature/key contract, so this client does not invent one.

## Protocol evidence and limits

Reviewed 2026-11-15 against official documentation:

- [OAuth guide](https://docs.gitcode.com/en/docs/apis/oauth/): authorization URL,
  callback state, code exchange and authenticated user lookup.
- [Code exchange API](https://docs.gitcode.com/docs/apis/post-oauth-token-grant-type-authorization-code-code-code-client-id-client-id-client-secret-client-secret/):
  code/client ID in the query, secret in multipart form data; token lifetime,
  creation timestamp and scopes in the response.
- [Current user API](https://docs.gitcode.com/docs/apis/get-api-v-5-user/): string
  `id` is the identity key. Mutable profile fields are not binding keys.

The client implements this documented wire shape with synthetic fixtures only.
No real account, app registration, provider request or production callback was
used. Scope names must be explicitly configured from the application's actual
registration; test scope names are synthetic, not an advertised scope catalog.
Unexpected provider responses fail closed rather than enabling a guessed
compatibility fallback. Self-hosted or alternate provider URLs are unsupported.

## Required before connecting login

A separately reviewed server-side coordinator must generate unpredictable state,
store a short-lived one-use transaction, bind it to the initiating browser and
exact callback, consume it atomically, reject duplicate callback parameters and
provider errors, and prevent replay/account-linking confusion. This client's
`authorization_url` validates **format**, not entropy or state authenticity.
`exchange_identity` must only receive the code from that validated transaction.

Provider support for PKCE and authorization-code injection protections still
needs authoritative/live capability verification. The inspected documentation
alone is not evidence of PKCE support or absence. Do not assume state alone
settles code-injection risks or enable a production callback before this gate.

The coordinator also needs application-bound sessions, rotation, CSRF protection,
secure cookies, expiry/revocation/logout and abuse controls, plus explicit durable
local identity bindings. Upstream organization membership must not silently grant
workspace roles. Real login, membership revocation and cross-workspace browser
acceptance remain **unverified**.

## Operational boundaries

Configuration and optional transport injection are trusted server inputs, never
request fields. Client secrets, codes and tokens live in memory only, but hidden
`repr` is not secret erasure or protection against debugger/exception-local dumps.
Never log request URLs, headers, bodies, responses or locals: the documented code
exchange puts the one-use code in the URL query. Restrict egress and disable
credential-bearing tracing at deployment. This client adds no logging.

The socket timeout is not an end-to-end deadline. An eventual service must bound
request duration, concurrency and rate independently. No real secrets or runtime
configuration should enter this public repository. Refresh, token storage,
implicit token verification, discovery and account provisioning are out of scope.

## Verification

```bash
python3 -m unittest discover -s packages/gitcode_identity/tests -v
```

All [tests](tests/README.md) are offline synthetic fixtures. The HTTPS transport
is tested through mocked connections, not live TLS/provider acceptance.

## Callback/session groundwork

The [login-state core](../login_state/README.md) supplies persistent one-use state,
browser binding, S256 material and session lifecycle primitives. It intentionally
does not enable this protocol client as a login verifier: a securely bound-code
adapter remains required; [session/policy integration](../session_api/README.md)
and the [unified host](../application_host/README.md) are now available as explicit
composition, without enabling this client.

See the [evidence ledger and staged acceptance plan](../../docs/gitcode-login-acceptance.md)
for the unresolved provider contract, controlled negative cases and activation
criteria. Offline gate regressions must not be reported as live GitCode acceptance.
