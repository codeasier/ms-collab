# Login-state tests

Offline synthetic tests for the [transaction/session core](../README.md).

A test-only server double binds synthetic codes to S256 challenges and rejects
injection/downgrade attempts. It does not establish GitCode PKCE support. The
suite covers state/browser/callback binding, duplicates, provider errors,
one-use concurrency across processes, persistent reopen, expiry, clock failures,
hashed token storage, rotation, CSRF, logout, revocation, capacities and corruption.

All runtime stores are new temporary files with synthetic identities. No real
provider, cookies, production data or credentials are used.
