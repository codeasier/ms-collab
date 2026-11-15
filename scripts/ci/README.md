# Public CI checks

- `check_repository.py`: tracked-file tripwire for common runtime/credential files
  and a limited set of credential markers. It never prints matching values.
- `test_check_repository.py`: synthetic regression tests for the checker.
- [CI trust boundary and commands](../../docs/public-ci.md).

These checks supplement human review; they do not certify a migration as safe.
