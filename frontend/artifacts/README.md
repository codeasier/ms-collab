# Artifact browser client

Separate same-origin read-only UI for a configured authorized artifact API.

- `browser.mjs`: injected-client scope/quarter/folder/view controller and stale-response guards.
- `entry.mjs`: standalone bearer-client construction only; never served by the
  [unified session entry](../../packages/application_ui/README.md).
- `client.mjs`: legacy bearer requests plus reusable exact catalog response validation.
- `index.html`, `style.css`: responsive text-only presentation, no external assets.
- [Tests](tests/README.md): unit and synthetic hosted browser acceptance.
- [Composition and security boundary](../../packages/artifacts_ui/README.md).
