# Contributing to ms-collab

Thanks for your interest in improving `ms-collab`!

## Ways to contribute

- Report bugs and propose features through GitHub Issues.
- Improve documentation.
- Submit pull requests for fixes and enhancements.

## Development workflow

1. Fork the repository and create a topic branch:
   `git checkout -b feat/short-description`
2. Make focused changes. Keep unrelated refactors out of the same pull request.
3. Ensure commits are clear and self-contained.
4. Open a pull request against `main` and fill in the template.
5. Address review feedback. A maintainer will merge once the change is approved.

## Commit messages

Use short, imperative subjects, optionally following Conventional Commits, e.g.:

- `feat: add collaboration room API`
- `fix: handle empty participant list`
- `docs: clarify setup steps`

## Pull requests

- Describe the problem and the chosen approach.
- Link related issues (e.g. `Closes #12`).
- Keep the diff reviewable; split large changes into stackable PRs.
- Update documentation when behavior changes.

## Reporting security issues

Do not open a public issue for vulnerabilities. See [SECURITY.md](SECURITY.md).

## Code of Conduct

All participants are expected to follow our
[Code of Conduct](CODE_OF_CONDUCT.md).

## Migration contributions

- Agree on an explicit file allowlist and preserve required copyright and license
  notices before importing code, examples, or documentation.
- Do not copy an existing repository's complete history, production configuration,
  private logs, credentials, or real organizational data.
- Use synthetic examples. Review screenshots, CI logs, and artifacts as well as
  source files for sensitive content.
- Report what was tested and what remains unverified; a document or menu entry is
  not proof that a feature is implemented.
- Untrusted pull requests must not execute on persistent infrastructure with
  production credentials, network access, or shared state.
- See the [migration acceptance gates](docs/migration-plan.md) before proposing an
  application-module import.
