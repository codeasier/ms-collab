# Scoped responsibility service tests

Run `python3 -m unittest discover -s packages/responsibility_api/tests -v`.
Real temporary SQLite transactions and loopback WSGI, synthetic identities and
community snapshots only. No upstream network or production credentials.

Covers exact scope collisions, two module gates, independent read/editor/publisher
grants, current owner membership, config CAS/restart, current exact-content
approval, stale observation invalidation, no unapproved counts/paths, strict
inputs, real HTTP, concurrent writers, schema corruption and audit/expiry rollback.

[Service](../README.md) · [Pure tests](../../responsibility/tests/README.md)
