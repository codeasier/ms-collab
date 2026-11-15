# Public CI and contribution trust boundary

## What this workflow does

`.github/workflows/public-ci.yml` runs on pull requests, pushes to `main`, and
manual dispatch. `Repository safety checks` uses a fresh GitHub-hosted
`ubuntu-24.04` runner, a read-only `GITHUB_TOKEN`, a full-SHA-pinned checkout action,
and `persist-credentials: false`. It has a ten-minute timeout and cancels stale
runs for the same PR/ref.

The initial checks use Python's standard library, with no package installation:

```sh
python3 scripts/ci/check_repository.py
python3 -m unittest discover -s scripts/ci -p 'test_*.py' -v
```

The checker inspects Git-tracked files, rejects common runtime/credential paths
and some private-key/token patterns, and prints reasons without matched values.
An environment template is allowed only as `.env.example` or `.env.template`;
its contents still require review and are scanned by the same limited patterns.
Add intended new files to the Git index before running the local check.

This is a **tripwire, not a complete secret scanner**. It does not establish that
history, screenshots, logs, fixtures, dependencies, external services, or all
credential formats are safe. Content already pushed to a public branch is public
before CI can run. Review and scan migration batches locally **before pushing**;
if a real credential escapes, revoke/rotate it and handle exposure, not merely
remove the current file. Do not paste the credential into an issue or CI log.

## What public PR jobs must never inherit

- No self-hosted runner, private network tunnel, production mount or persistent
  workspace from existing services.
- No repository/organization secrets, cloud credentials, environment deployment
  permissions, or `id-token: write`.
- No `pull_request_target` or privileged `workflow_run` job executing PR code.
- No shared dependency cache populated by untrusted code and later consumed by a
  privileged job; this initial workflow uses no cache or uploaded artifacts.
- No deployment, publishing, webhook notification, or repository write-back.

GitHub-hosted is not "networkless": these runners may access the public Internet.
Keep data synthetic and never add a route to internal infrastructure. Fork-run
approval is useful governance but is not a substitute for runtime isolation.
The GitHub token is transient and read-only, not a production credential.

## Maintainer setup and review

This PR changes workflow files only, not repository settings. Before treating
checks as an enforced merge gate, the owner must verify repository/organization
Actions permissions, fork approval policy, secret access, and a branch ruleset
requiring the relevant checks and review. Their current settings are not verified
by this document. Review workflow changes as security-sensitive, especially
permissions, event triggers, runner selection and checkout refs.

The separate `Demo tests when present` job pins Python 3.12 and Node 22 setup
actions by full SHA. It uses no dependency installation or package-manager cache.
For the initial documentation-only revision, absent `backend/` or `frontend/`
directories are explicitly reported as not migrated, not as tested applications.
Once those directories exist, missing tests fail rather than silently skipping:

```sh
python3 -m unittest discover -s backend/tests -v
node --test frontend/tests/*.test.mjs
```

Feature PRs must add their own focused unit/build/integration checks as code
arrives. A green repository check alone does not mean the application builds,
workspace isolation works, or production deployment is approved.

Reference: [GitHub Actions security guidance](https://docs.github.com/en/actions/reference/security/secure-use).
