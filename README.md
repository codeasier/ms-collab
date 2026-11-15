# ms-collab

An open-source organizational collaboration and CI/CD workbench for MindStudio toolchain teams.

Independently maintained by codeasier. This is not an official MindStudio product
and does not imply official endorsement.

> Status: reviewed service/UI slices and a separate local demo are available.
> Real-provider and production migration acceptance are not complete.

## Overview

`ms-collab` aims to provide a clean, well-documented foundation for collaborative
tooling. This repository contains a local synthetic demo plus an explicitly
configured cookie/session service and modular UI. The service slices have
synthetic automated acceptance, not live-provider or deployment acceptance.
They are not yet a production replacement for existing services.

## Product direction

- Isolated workspaces, each with its own administrators, members, and configuration.
- A public workspace for explicitly authorized cross-team collaboration, not
  automatic access to private workspace data.
- Collaboration, responsibility areas, CI, artifacts, and development assistance
  as capabilities that can be enabled separately within each workspace.
- Clear boundaries between platform governance and workspace self-service.

The configured service implements workspace policy/persistence, administrator
workflows and scoped modules with a shared cookie/session entry. See the
[capability map and limits](docs/unified-entry.md) for what each page actually does.
Real identity-provider integration and production isolation/deployment still need
separate acceptance. The loopback demo below remains deliberately synthetic and
read-only; it does not activate that service composition.

## Migration approach

Code will enter this repository in reviewed, allowlisted batches with a clean
history. Existing services remain unchanged. Production configurations, private
operations records, credentials, runtime data, and old repository history are
outside the public migration scope.

- [Run the local demo](docs/runnable-demo.md)
- [Documentation index](docs/README.md)
- [Migration plan](docs/migration-plan.md)
- [Workspace design goals](docs/workspaces.md)

## Getting Started

```bash
git clone https://github.com/codeasier/ms-collab.git
cd ms-collab
PYTHONPATH=backend python3 -m ms_collab --port 8000
```

Requires Python 3.12+. Open `http://127.0.0.1:8000`. No build or dependency
installation is required. The demo is loopback-only and must not receive real
data or be deployed to production. See [demo verification](docs/runnable-demo.md)
and [CONTRIBUTING.md](CONTRIBUTING.md).

## Contributing

Contributions are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) and
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) before opening an issue or pull request.
For security reports, follow [SECURITY.md](SECURITY.md) instead of filing a public
issue.

## License

Released under the [MIT License](LICENSE).

## Configured installation tooling

The [service runtime](packages/service_runtime/README.md) adds strict configuration,
explicit empty-store initialization, read-only installation checks, cold backups,
new-directory restore and a fixed loopback TLS process. This is distinct from the
local demo. Real GitCode login, production enrollment and deployment acceptance
remain gated; a valid installation is not a production-ready service.
