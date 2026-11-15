# Demo workbench

A static, dependency-free shell served by the [local demo](../backend/README.md).
No build or package installation is required. Node.js 22+ is needed only for tests:

```sh
node --test frontend/tests/*.test.mjs
```

- `navigation.mjs`: capability filtering adapted from the predecessor's modular
  navigation, copyright 2026 codeasier, MIT. Unlike the predecessor's optional
  capability entries, every demo item requires an explicit true capability.
- `app.mjs`: new demo composition, scoped overview/board requests and cancellation of stale loads.
- `board.mjs`: bounded query construction and scoped response validation.
- `index.html` / `style.css`: new synthetic workbench UI.

Navigation filtering is presentation only. API authorization belongs to the
server; fixed synthetic membership checks do not constitute production identity
or multi-tenant isolation.

- [Optional browser acceptance](checks/README.md) is separate from the dependency-free unit checks.
