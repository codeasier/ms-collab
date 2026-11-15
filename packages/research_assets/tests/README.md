# Research-asset tests

Run `python3 -m unittest discover -s packages/research_assets/tests -v` from the
repository root. Temporary SQLite stores, fixed clocks, fictional identities and
normalized synthetic source documents exercise real policy/service transactions.
No source checkout, network credentials, production corpus or runbook execution.

[Catalog contract and limits](../README.md)


- `test_assets.py`: projection, scoped service reads and complete multi-source
  atomic publication, bounded data, failure rollback and current policy.
- `test_flow_authority.py`: realm/generation/permission-revision admission fences,
  before/after source checks, identity and authorization ABA, independent-realm
  replacement and actual writer-lock waiting. The closed identity mutation
  guard is bypassed only by the shared disposable test simulator. No production
  mutation, provider-internal network control or backup anti-rollback is claimed.
