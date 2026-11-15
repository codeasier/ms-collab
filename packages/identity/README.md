# Trusted identity adapter boundary

Status: tested server-side interface, **not production authentication**. This
package is not connected to the demo HTTP routes. It contains no OAuth provider,
JWT decoder, network client, credential store, login/session endpoint or default
identity. The only verifier doubles live in [tests](tests/README.md).

## Boundary

1. A trusted server configuration registers a `Provider` with an exact HTTPS
   issuer, application audience and verifier implementation. A request can choose
   only an allowlisted key, never a discovery URL, algorithm or issuer.
2. `Authenticator.authenticate` passes an opaque `Credential` to that verifier.
   The verifier must perform the actual protocol verification and produce a
   `VerifiedIdentity`. Instantiating that dataclass proves nothing by itself.
3. The boundary checks exact issuer/audience and a bounded, current assertion
   interval (timezone-aware, no future issue time, strict expiry). It checks time
   again after directory lookup, so a slow dependency cannot extend validity.
4. A trusted `PrincipalDirectory` resolves the current `(issuer, subject)` pair
   to an explicit local `Principal`. Unknown, disabled or mismatched identities
   are denied. There is no email/login-name matching, account auto-linking,
   default user, role import, workspace creation or member enrollment.
5. The caller still invokes workspace authorization for each operation. A
   returned `Principal` is only an identity snapshot for the current request,
   not a session, portable token or cached permission. Scope, membership, module
   state and resource ownership are checked by the current workspace policy.

`StaticPrincipalDirectory` is an immutable bootstrap adapter for offline/test
integration. It rejects duplicate local IDs and duplicate external pairs. It
cannot manage live accounts. A real adapter must load current bindings and
inactive state transactionally, without silently relinking a reused external ID.
No local principal IDs may be reused for a different external identity.

The directory and workspace policy must refer to the same authoritative records.
This package does **not** solve cross-store revocation races. Production
integration must recheck identity activity and membership in the transaction
that performs the protected read/write, including queued and asynchronous work.
Do not cache this result or accept a client-supplied local principal ID.

## Failure behavior

Invalid/missing credentials, unknown providers or bindings, inactive principals
and invalid assertions produce a generic `authentication_required` (401). A
failed trusted dependency or invalid clock produces `authentication_unavailable`
(503), never anonymous fallback. Provider exception text is suppressed; callers
must also avoid logging raw credentials, provider responses or exception locals.

`Credential` hides its value in `repr`, not in memory. It is not secure storage,
serialization protection or a secret erasure facility. Credentials are not
persisted or included in returned principals.

## Still required before a real login

- An independently reviewed provider implementing protocol-specific signature
  and key validation or authenticated code exchange, token type/audience checks,
  single-use code/state handling, nonce/PKCE where applicable, issuer binding,
  upstream expiry/revocation and fixed trusted endpoints with TLS/timeouts.
- Server-side session lifecycle, login CSRF protections, session rotation,
  secure cookies, logout/revocation, request limits and abuse controls.
- Current, durable identity binding and approved enrollment/linking procedures;
  no automatic conversion of upstream organization roles into workspace roles.
- HTTP integration and negative tests for forged headers, cookies, subjects and
  provider selection; transactional authorization and multi-process revocation.
- Only synthetic evidence in public tests. No real client configuration,
  credentials, account directory or production callback URLs.

## Verification

```bash
python3 -m unittest discover -s packages/identity/tests -v
```

[Migration boundaries](MIGRATION.md) · [Workspace policy](../workspaces/README.md)
· [Documentation](../../docs/README.md)

## Transactional consumers

`CredentialVerifier.verify` extracts the protocol verification and current-time
checks used by `Authenticator`. `validate_current` only rechecks an already
verified in-process assertion; it does not authenticate arbitrary claims.
The [workspace API](../workspace_api/README.md) uses this separation to perform
external verification before a SQLite lock, then resolve the current binding and
reauthorize inside the policy transaction. It has no endpoint that accepts a
`VerifiedIdentity` or `Principal` from a client. Existing `Authenticator` behavior
and generic directory integrations remain unchanged.

## Provider protocol groundwork

The [GitCode OAuth protocol client](../gitcode_identity/README.md) implements
only documented wire operations and minimized upstream evidence. It is **not** a
`Provider` verifier or login route; callback transaction binding, protocol
capability verification and sessions remain required before integration.
