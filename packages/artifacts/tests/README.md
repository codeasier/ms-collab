# Artifact tests

Run `python3 -m unittest discover -s packages/artifacts/tests -v` from the root.
All metadata is synthetic; no provider, filesystem, secrets or database is used.

Coverage: quarter/timezone boundaries; complete-input validation; literal path
semantics and virtual directory markers; stable sorting/pagination; exact three-part
scope and no public union; explicit missing archives; literal condition operators,
path/time boundaries; named-view scope mismatch and no fallback; immutable inputs
and detached response dictionaries.

These tests do not certify authentication, persistent view sharing, synchronization,
provider permissions, UI, download access or production isolation.
