# Artifact administration tests

- `test_application.py`: current permissions, whole-candidate/reader approval,
  revision races, corruption/framing, provider errors and fixed-origin wrapper.
- `browser_server.py`: temporary SQLite store and synthetic split refresh/approve
  identities, observed XML and reader application. Never use it in production.

Run from the root:

```bash
python3 -m unittest discover -s packages/artifact_admin/tests -v
python3 -m unittest discover -s packages/artifact_sync/tests -v
```

[API/UI contract](../README.md) · [Hosted browser scenarios](../../../frontend/artifact-admin/tests/README.md)
