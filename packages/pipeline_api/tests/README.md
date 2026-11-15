# Pipeline API synthetic tests

`python3 -m unittest discover -s packages/pipeline_api/tests -v`

Tests create temporary SQLite stores with fictional identities, scopes and runs.
They cover a real loopback HTTP read, strict routes/queries/codec, list and detail
projection, duration semantics, no-cookie authority, workspace isolation,
explicit shared-space membership, disabled modules, archived reads, revocation,
identity re-binding, assertion expiry and safe failures. A deterministic barrier
checks revocation while verification is pending. Unauthorized records are never
decoded. Reads leave resources and audit unchanged; mutation/management paths
are absent. Resource corruption never becomes a successful empty list.

The verifier and token-like test strings are local doubles, not production
credentials. No live identity/CI provider, production data or external network is
used. [API boundary and remaining work](../README.md)
