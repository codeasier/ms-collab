# Artifact browser client

Separate same-origin read-only UI for a configured authorized artifact API.

- `browser.mjs`: scope/quarter/folder/view lifecycle and stale-response guards.
- `client.mjs`: bearer requests and exact response validation.
- `index.html`, `style.css`: responsive text-only presentation, no external assets.
- [Tests](tests/README.md): unit and synthetic hosted browser acceptance.
- [Composition and security boundary](../../packages/artifacts_ui/README.md).
