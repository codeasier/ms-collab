# Configured artifact refresh

- [Security and lifecycle contract](DESIGN.md)
- [Persistent admission and bounded refresh](service.py)
- [Fixed bounded OBS page transport](transport.py)
- [Synthetic verification](tests/README.md)

An explicit cookie-session host integration over the existing artifact index and
whole-candidate approval service. No credentials, production data, deployment or
live-provider acceptance is included. Refresh never implicitly publishes metadata
or grants byte downloads. Trusted setup and current per-scope permissions remain
required.

## Trusted setup

After explicitly provisioning the existing workspace, session and index policies:

```python
from packages.artifact_refresh import ArtifactRefreshConfiguration, initialize_refresh
from packages.application_host import ApplicationHost, HostConfig

initialize_refresh(sessions.policy)  # Trusted setup, never on a request.
app = ApplicationHost(
    config=HostConfig(origin="https://collab.example.invalid", modules=("artifacts",),
                      artifact_administration=True, artifact_refresh=True),
    sessions=sessions,
    artifact_refresh=ArtifactRefreshConfiguration(scoped_secret_loader),
)
```

The deliberately unspecified `sessions` and `scoped_secret_loader` are approved
host dependencies, not code to copy from a fixture. No login exchange is supplied
by this example. Default OBS transport permits only fixed public regional hosts,
identity encoding and a unique Content-Length XML response; other provider
framing is rejected until independently supported and verified.

POST refresh uses `{}` and the existing `If-Match`; additional fields cannot
select a remote target. 429 means acquisition capacity is occupied, 504 means
the guarded refresh deadline expired, and 502/503 are redacted provider/storage
failures. Never automatically retry a write with an uncertain response. Reload
current state and inspect the complete candidate before approving.
