# Artifact index tests

- `test_provider.py`: synthetic OBS wire protocol, strict metadata parsing,
  pagination/window bounds, credentials, DNS pinning and GET-only transport.
- `test_service.py`: actual temporary SQLite policy/index transactions, explicit
  publication, live authorization negative cases, corruption, races and HTTP.
- `fixtures.py`: unmistakably synthetic identities, signing input and XML only.
- `browser_server.py` + `browser.cjs`: acquisition/approval/index-to-browser path;
  a newer private candidate must not appear in the published UI. No real provider
  request, credential or production dataset is used.

Run `python3 -m unittest discover -s packages/artifact_sync/tests -v` from the root.
The dedicated `Artifact index acquisition` workflow also runs Playwright against
this synthetic server and retains desktop/mobile screenshots for three days.

[Package contract](../README.md) · [Protocol provenance](../MIGRATION.md)
