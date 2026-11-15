"""Print a fully synthetic duration analysis; never read files or providers."""
import json
from . import PipelineScope, summarize_durations


def main():
    scope = PipelineScope('product-alpha', 'example-ci', 'build')
    snapshots = [{**scope.as_dict(), 'id': 'run-1', 'observed_at': '2026-01-01T01:00:00Z',
                  'status': 'failure', 'stages': [{'name': 'compile', 'jobs': [
                      {'id': 'job-1', 'name': 'linux', 'status': 'success',
                       'started_at': '2026-01-01T00:00:00Z', 'finished_at': '2026-01-01T00:01:00Z'},
                      {'id': 'job-2', 'name': 'other', 'status': 'failure'},
                  ]}]}]
    print(json.dumps(summarize_durations(scope, snapshots), indent=2))


if __name__ == '__main__':
    main()
