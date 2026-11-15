# Connection tests

Run `python3 -m unittest discover -s packages/workspace_connections/tests -v`.
Fixtures reuse explicit synthetic community policies and never contact a provider.
`test_connections.py` covers current authority, platform ceilings, scope discovery,
revision conflicts, observation/approval invalidation and same-transaction audit.
See the package [contract](../README.md) for what is not provisioned or verified.
