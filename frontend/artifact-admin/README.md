# Artifact administration browser

Current administrator capabilities, a private refresh action, whole-candidate
review and explicit exact-audience approval. The browser recomputes the complete
snapshot digest, never treats a filtered page as the approval set, and clears
private state on scope changes, conflicts, expiration and disconnect.

Serve only through the configured
[administration wrapper](../../packages/artifact_admin/README.md), not a default
identity or demo credential. The wrapper uses a fixed origin and CSP; credentials
are kept only in memory. No file downloads, arbitrary endpoints or credential
management are implemented here.

[Tests and hosted acceptance](tests/README.md).
