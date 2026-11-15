# GUI observation presentation

- `client.mjs`: strict scope/snapshot/detail validators and cancellable view state.
- `view.mjs`: injected-client page presentation for the unified entry; no separate
  authentication flow, storage, provider URL or token conversion.
- `app.mjs`, `index.html`: existing standalone cookie UI, retained independently.
- `style.css`: literal-text responsive observation layout.
- [Tests](tests/README.md): observation contracts and synthetic browser checks.

The [unified composition](../../packages/application_ui/README.md) shares the
current server session and passes only read requests to the existing scoped API.
Published task reports are not independently verified execution or a release gate.
No execution, refresh, raw logs or download is added.
