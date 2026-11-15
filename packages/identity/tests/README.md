# Identity boundary tests

Run `python3 -m unittest discover -s packages/identity/tests -v` from the root.

All identities, timestamps and credentials are synthetic. `TestVerifier` and
`ChangingDirectory` are isolated test doubles, not deployable providers.

Tests cover default denial, exact issuer/audience, explicit local bindings,
expiry before and during lookup, changed/deleted accounts, safe dependency
failures, malformed inputs and independent workspace authorization. They do not
validate a real OAuth/OIDC exchange, cryptographic verification, sessions,
provider revocation, HTTP authentication or multi-process identity transactions.

[Package boundary](../README.md)
