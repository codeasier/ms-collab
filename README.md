# ms-collab

An open-source organizational collaboration and CI/CD workbench for MindStudio toolchain teams.

Independently maintained by codeasier. This is not an official MindStudio product
and does not imply official endorsement.

> Status: early stage. The project scope and roadmap are being defined.

## Overview

`ms-collab` aims to provide a clean, well-documented foundation for collaborative
tooling. This repository currently contains the project scaffolding only; code and
specifications will be added as the design is finalized.

## Product direction

- Isolated workspaces, each with its own administrators, members, and configuration.
- A public workspace for explicitly authorized cross-team collaboration, not
  automatic access to private workspace data.
- Collaboration, responsibility areas, CI, artifacts, and development assistance
  as capabilities that can be enabled separately within each workspace.
- Clear boundaries between platform governance and workspace self-service.

These are design goals, **not implemented or verified features**. This repository
does not yet contain a runnable application, production deployment, or tested
workspace isolation.

## Migration approach

Code will enter this repository in reviewed, allowlisted batches with a clean
history. Existing services remain unchanged. Production configurations, private
operations records, credentials, runtime data, and old repository history are
outside the public migration scope.

- [Documentation index](docs/README.md)
- [Migration plan](docs/migration-plan.md)
- [Workspace design goals](docs/workspaces.md)

## Getting Started

```bash
git clone https://github.com/codeasier/ms-collab.git
cd ms-collab
```

Nothing to build yet. See [CONTRIBUTING.md](CONTRIBUTING.md) if you would like to
help shape the project.

## Contributing

Contributions are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) and
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) before opening an issue or pull request.
For security reports, follow [SECURITY.md](SECURITY.md) instead of filing a public
issue.

## License

Released under the [MIT License](LICENSE).
