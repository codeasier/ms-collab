# Pipeline provider acquisition tests

Run `python3 -m unittest discover -s packages/pipeline_sync/tests -v`.

All provider replies, identities and tokens are fictional; sockets are intercepted.
Temporary SQLite stores test atomic authorized local publication, scope isolation,
revocation/expiry between network reads and publication, stale revisions, provider
failures, strict projection and existing pipeline API integration. Transport tests
cover pinned public DNS, TLS hostname, fixed GET paths, header-only credentials,
redirect/error/compression refusal and byte/schema limits. No live provider or
production compatibility is claimed. [Boundary](../README.md)
