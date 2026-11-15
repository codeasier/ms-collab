# Download acceptance tests

[Package contract](../README.md)

Run `python3 -m unittest discover -s packages/artifact_download/tests -v` from the
repository root. Inputs are synthetic, including all identities, signed material,
object bytes and OBS protocol responses. No cloud account or real object is read.

- [Authorization, persistent admission, real HTTP and stream lifecycle](test_download.py)
- [Signing, response shape, DNS/TLS, deadline and binding bounds](test_object_transport.py)

- [Cookie/session admission and continuous revocation](test_session.py)
- [Synthetic HTTPS browser fixture](session_browser_server.py), explicitly gated;
  never imported by production code. It uses real local session/SQLite/lease paths
  but only in-memory synthetic object bytes and controlled provider responses.

A focused pass does not replace complete repository checks or real-provider
acceptance. [Browser acceptance](../../../frontend/application/tests/artifact-downloads.cjs)
verifies a native browser download's complete saved bytes, stale confirmation,
late fetch/digest cancellation, logout/cleanup and five synthetic screenshots.
No screenshot or synthetic file is evidence of real cloud downloads.
