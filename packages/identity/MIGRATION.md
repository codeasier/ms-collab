# Identity migration boundaries

This package is a new, provider-neutral boundary. It does not transplant an old
application's complete authentication controller, database or token store.

## Preserve the security properties, separate the responsibilities

| Responsibility | Public increment | Deferred integration |
| --- | --- | --- |
| External identity proof | Required trusted verifier, exact issuer/audience, bounded assertion validity | Real provider protocol implementation and live integration |
| Local identity binding | Explicit issuer + stable subject pair to local ID, no name/email matching | Durable bindings and approved enrollment/linking workflow |
| Current account state | Resolve every authentication attempt; disabled/unknown denied | Single authoritative transaction with policy and revocation |
| Workspace permissions | No implicit membership or role from successful login | Current workspace/module/resource authorization at every entry point |
| Safe failures | Generic denial/unavailable errors without raw provider text | Operational telemetry with redaction and abuse controls |
| Web/session behavior | Not supplied | State/redirect/CSRF handling, session fixation protection, cookies, expiry and logout |
| Provider tokens | Opaque input only; not stored, copied into profile or returned | Separate encrypted credential storage, least privilege and rotation |

A provider-specific organization admission rule is not a general workspace
membership model. An organization response, display name or email must never
silently grant administrator privileges in a workspace. Existing organizational
admission restrictions need an explicit migration policy, not removal by default.

Do not import existing session cookies, signing keys, provider tokens, user rows,
API keys or default production organizations. A clean development fixture is not
a migration of production identity. Preserve prior production behavior until an
independently tested replacement and rollback plan exist.

A future provider cannot turn arbitrary JSON or a decoded unsigned token into
`VerifiedIdentity`. This type is an internal post-verification contract, not an
HTTP payload schema. The package ships no permissive verifier to accidentally
enable in production.

[Package scope](README.md) · [Tests](tests/README.md)
