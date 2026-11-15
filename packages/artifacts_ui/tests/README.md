# Artifact UI synthetic checks

- `test_ui.py`: configured factory, exact assets, origin/host/scheme controls,
  policy authority, cross-space denial and absence of management/write routes.
- `browser_server.py`: temporary SQLite fixtures and a test-only verifier for the
  hosted browser check. Not a deployable application or real provider.

Run `python3 -m unittest discover -s packages/artifacts_ui/tests -v`.
[Browser checks](../../../frontend/artifacts/tests/README.md) · [Boundary](../README.md).
