# Scoped responsibility service tests

Run `python3 -m unittest discover -s packages/responsibility_api/tests -v`.
Real temporary SQLite transactions and loopback WSGI, synthetic identities and
community snapshots only. No upstream network or production credentials.

Covers exact scope collisions, two module gates, independent read/editor/publisher
grants, current owner membership, config CAS/restart, current exact-content
approval, stale observation invalidation, no unapproved counts/paths, strict
inputs, real HTTP, concurrent writers, schema corruption and audit/expiry rollback.

[Service](../README.md) · [Pure tests](../../responsibility/tests/README.md)


`test_authority.py` covers generation-bound reader/editor/publisher grants,
historical owners/committers/cross-field backups, retired-ID reuse, active/member
ABA, explicit proposal reconfirmation, unrelated authority boundaries, all six
mixed-format failures and destructive legacy recovery. Editor-nonowner role ABA,
empty-config foreign realm, both module cycles, community regrant, actual writer
reservation waits and CAS races are checked. Persistence expiry/audit failures,
MAX revisions, canonical JSON, size/history bounds and publisher-provenance
invalidation retain the prior records on failure. Test-only directory transitions
never become production mutation helpers. [Full contract](../REFERENCES.md).
