# Artifact administration browser

Current administrator capabilities, a private refresh action, whole-candidate
review and explicit exact-audience approval. The browser recomputes the complete
snapshot digest, never treats a filtered page as the approval set, and clears
private state on scope changes, conflicts, expiration and disconnect.

The injected-client `browser.mjs` controller can also run through the configured
[unified cookie entry](../../packages/application_ui/README.md), with no credential
input or token conversion. `entry.mjs` alone creates the legacy bearer client.

Serve the standalone page only through the configured
[administration wrapper](../../packages/artifact_admin/README.md), not a default
identity or demo credential. The wrapper uses a fixed origin and CSP; credentials
are kept only in memory. No file downloads, arbitrary endpoints or credential
management are implemented here.

[Tests and hosted acceptance](tests/README.md).
