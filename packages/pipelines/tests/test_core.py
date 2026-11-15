"""Synthetic regression tests: no upstream data, sockets, databases or credentials."""
import copy
import json
import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

from packages.pipelines import InvalidSnapshot, PipelineScope, normalize_status, summarize_durations, timestamp

SCOPE = PipelineScope('product-alpha', 'example-ci', 'build')


def job(**updates):
    data = {'id': 'job-1', 'name': 'linux', 'status': 'success',
            'started_at': '2026-01-01T00:00:00Z', 'finished_at': '2026-01-01T00:01:00Z'}
    return {**data, **updates}


def run(number=1, **updates):
    data = {**SCOPE.as_dict(), 'id': f'run-{number}', 'status': 'success',
            'observed_at': f'2026-01-{number:02d}T02:00:00Z',
            'stages': [{'name': 'build', 'jobs': [job()]}]}
    return {**data, **updates}


class StatusTests(unittest.TestCase):
    def test_explicit_aliases(self):
        for raw, expected in [('SUCCESS', 'success'), ('succeeded', 'success'), ('Passed', 'success'),
                              ('FAILED', 'failure'), ('error', 'failure'), ('CANCELED', 'cancelled'),
                              ('ABORTED', 'cancelled'), ('TIMEOUT', 'timed_out'),
                              ('skipped', 'skipped'), ('PENDING', 'queued'), ('in_progress', 'running')]:
            self.assertEqual(normalize_status(raw), expected)

    def test_completed_requires_explicit_conclusion(self):
        self.assertEqual(normalize_status('completed'), 'unknown')
        self.assertEqual(normalize_status('completed', 'failure'), 'failure')
        self.assertEqual(normalize_status('completed', 'success'), 'success')

    def test_conflicts_and_malformed_stay_unknown(self):
        for status, conclusion in [('running', 'success'), ('success', 'failure'), ('queued', 'failure'),
                                   ('completed', 'running'), ('new_value', 'success'),
                                   (True, None), (['success'], None), ('completed', True),
                                   ('completed', {}), ('completed', '')]:
            self.assertEqual(normalize_status(status, conclusion), 'unknown')

    def test_same_terminal_evidence(self):
        self.assertEqual(normalize_status('FAILED', 'failure'), 'failure')


class TimestampTests(unittest.TestCase):
    def test_explicit_offset_normalizes_utc(self):
        self.assertEqual(timestamp('2026-01-01T08:00:00+08:00'), timestamp('2026-01-01T00:00:00Z'))

    def test_no_implicit_timezones_or_units(self):
        for value in [None, True, 0, 1700000000, 1700000000000, float('nan'), float('inf'),
                      '1700000000', '2026-01-01', '2026-01-01T00:00:00', 'bad', 'x' * 65,
                      '0001-01-01T00:00:00+01:00']:
            self.assertIsNone(timestamp(value))

    def test_zero_and_fractional_duration(self):
        rows = [run(stages=[{'name': 'build', 'jobs': [job(finished_at='2026-01-01T00:00:00Z')]}])]
        self.assertEqual(summarize_durations(SCOPE, rows)['groups'][0]['min_seconds'], 0)
        rows[0]['stages'][0]['jobs'][0]['finished_at'] = '2026-01-01T00:00:00.250Z'
        self.assertEqual(summarize_durations(SCOPE, rows)['groups'][0]['latest_seconds'], .25)


class ScopeTests(unittest.TestCase):
    def test_required_scope_and_immutable(self):
        for value in ['', ' white', True, None, '../scope', 'a/b', 'a' * 129]:
            with self.assertRaisesRegex(InvalidSnapshot, '^invalid_scope$'):
                PipelineScope(value, 'ci', 'build')
        with self.assertRaises(FrozenInstanceError):
            SCOPE.workspace_id = 'other'
        with self.assertRaisesRegex(InvalidSnapshot, '^invalid_scope$'):
            summarize_durations({}, [])

    def test_each_scope_dimension_rejects_before_limit_and_status(self):
        for key in SCOPE.as_dict():
            for value in ['other', None]:
                with self.assertRaisesRegex(InvalidSnapshot, '^scope_mismatch$'):
                    summarize_durations(SCOPE, [run(), run(2, **{key: value, 'status': 'running'})], limit=1)

    def test_public_scope_not_union(self):
        public = PipelineScope('commons', 'example-ci', 'build')
        with self.assertRaisesRegex(InvalidSnapshot, '^scope_mismatch$'):
            summarize_durations(public, [run()])

    def test_duplicate_run_id_not_double_counted(self):
        with self.assertRaisesRegex(InvalidSnapshot, '^duplicate_run_id$'):
            summarize_durations(SCOPE, [run(), run(status='running')])

    def test_missing_identity_and_observation_rejected(self):
        for value in [None, True, 1, {}, 'wrong\nvalue']:
            with self.assertRaisesRegex(InvalidSnapshot, '^invalid_run_id$'):
                summarize_durations(SCOPE, [run(id=value)])
        with self.assertRaisesRegex(InvalidSnapshot, '^invalid_observation_time$'):
            summarize_durations(SCOPE, [run(observed_at='2026-01-01T00:00:00')])

    def test_no_raw_values_in_errors(self):
        with self.assertRaises(InvalidSnapshot) as result:
            summarize_durations(SCOPE, [run(id='unsafe\nmessage')])
        self.assertEqual(str(result.exception), 'invalid_run_id')

    def test_bounded_inputs(self):
        for limit in [0, 501, True, 1.1, None]:
            with self.assertRaisesRegex(InvalidSnapshot, '^invalid_limit$'):
                summarize_durations(SCOPE, [], limit=limit)
        for rows in [iter([]), {}, [run()] * 5001]:
            with self.assertRaisesRegex(InvalidSnapshot, '^invalid_snapshot_set$'):
                summarize_durations(SCOPE, rows)
        with self.assertRaisesRegex(InvalidSnapshot, '^invalid_snapshot$'):
            summarize_durations(SCOPE, [None])
        with self.assertRaisesRegex(InvalidSnapshot, '^too_many_stages$'):
            summarize_durations(SCOPE, [run(stages=[{}] * 201)])
        with self.assertRaisesRegex(InvalidSnapshot, '^too_many_jobs$'):
            summarize_durations(SCOPE, [run(stages=[{'jobs': [None] * 10001}])])


class DurationTests(unittest.TestCase):
    def test_empty_is_not_complete_history(self):
        result = summarize_durations(SCOPE, [])
        self.assertEqual(result['groups'], [])
        self.assertEqual(result['coverage']['selected_runs'], 0)
        self.assertFalse(result['coverage']['full_history_complete'])

    def test_successful_jobs_in_failed_and_cancelled_runs(self):
        result = summarize_durations(SCOPE, [run(status='failed'), run(2, status='cancelled')])
        group, = result['groups']
        self.assertEqual(group['count'], 2)
        self.assertEqual([s['run_status'] for s in group['samples']], ['failure', 'cancelled'])

    def test_running_and_unknown_runs_not_selected(self):
        result = summarize_durations(SCOPE, [run(status='running'), run(2, status='COMPLETED')])
        self.assertEqual(result['groups'], [])
        self.assertEqual(result['coverage']['nonterminal_or_unknown_runs'], 2)

    def test_completed_job_needs_success_conclusion(self):
        result = summarize_durations(SCOPE, [run(stages=[{'name': 'build', 'jobs': [
            job(status='completed'), job(id='job-2', name='other', status='completed', conclusion='success')]}])])
        self.assertEqual(result['groups'][0]['job_name'], 'other')
        self.assertEqual(result['coverage']['excluded_jobs'], 1)

    def test_failed_skipped_and_cancelled_jobs_excluded(self):
        jobs = [job(id=f'job-{i}', status=status) for i, status in enumerate(['failed', 'skipped', 'cancelled'])]
        result = summarize_durations(SCOPE, [run(stages=[{'name': 'build', 'jobs': jobs}])])
        self.assertEqual(result['groups'], [])
        self.assertEqual(result['coverage']['excluded_jobs'], 3)

    def test_newest_observed_terminal_window_and_tie_break(self):
        rows = [run(), run(2), run(3, status='running')]
        result = summarize_durations(SCOPE, rows, limit=1)
        self.assertEqual(result['groups'][0]['samples'][0]['run_id'], 'run-2')
        self.assertEqual(result['coverage']['observed_terminal_runs'], 2)
        self.assertTrue(result['coverage']['selection_truncated'])
        rows[1]['observed_at'] = rows[0]['observed_at']
        self.assertEqual(summarize_durations(SCOPE, rows, limit=1)['groups'][0]['samples'][0]['run_id'], 'run-2')

    def test_limit_is_not_backfilled_from_older_complete_details(self):
        result = summarize_durations(SCOPE, [run(), run(2, stages=None)], limit=1)
        self.assertEqual(result['groups'], [])
        self.assertEqual(result['coverage']['missing_details'], 1)

    def test_missing_details_reported(self):
        rows = [run(i + 1, stages=value) for i, value in enumerate([None, {}, [], 'invalid'])]
        self.assertEqual(summarize_durations(SCOPE, rows)['coverage']['missing_details'], 4)

    def test_invalid_timestamps_not_zero_filled(self):
        for updates in [{'started_at': None}, {'finished_at': 'bad'}, {'started_at': True},
                        {'started_at': '2026-01-01T01:00:00Z'}, {'id': None}, {'id': []}]:
            result = summarize_durations(SCOPE, [run(stages=[{'name': 'build', 'jobs': [job(**updates)]}])])
            self.assertEqual(result['groups'], [])
            self.assertEqual(result['coverage']['invalid_duration_jobs'], 1)

    def test_successful_ambiguous_names_not_combined(self):
        result = summarize_durations(SCOPE, [run(stages=[{'name': 'build', 'jobs': [job(), job(id='job-2')]}])])
        self.assertEqual(result['groups'], [])
        self.assertEqual(result['coverage']['ambiguous_jobs'], 2)

    def test_invalid_successful_sibling_still_ambiguous(self):
        for updates in [{'started_at': None}, {'finished_at': None}, {'id': None}, {'id': []}]:
            result = summarize_durations(SCOPE, [run(stages=[{'name': 'build', 'jobs': [job(), job(**updates)]}])])
            self.assertEqual(result['groups'], [])
            self.assertEqual(result['coverage']['ambiguous_jobs'], 2)

    def test_same_named_stages_ambiguous_but_different_stages_separate(self):
        for name, count in [('build', 0), ('test', 2)]:
            stages = [{'name': 'build', 'jobs': [job()]}, {'name': name, 'jobs': [job(id='job-2')]}]
            self.assertEqual(len(summarize_durations(SCOPE, [run(stages=stages)])['groups']), count)

    def test_failed_sibling_not_successful_ambiguity(self):
        stages = [{'name': 'build', 'jobs': [job(), job(id='job-2', status='failure')]}]
        self.assertEqual(summarize_durations(SCOPE, [run(stages=stages)])['groups'][0]['count'], 1)

    def test_duplicate_job_identity_not_double_counted_across_names(self):
        stages = [{'name': 'build', 'jobs': [job(), job(name='other')]}]
        result = summarize_durations(SCOPE, [run(stages=stages)])
        self.assertEqual(result['groups'], [])
        self.assertEqual(result['coverage']['invalid_duration_jobs'], 2)

    def test_aggregation_statistics_and_chronological_samples(self):
        rows = [run(i + 1, observed_at='2026-02-01T00:00:00Z', stages=[{'name': 'build', 'jobs': [job(
            started_at=f'2026-01-{3-i:02d}T00:00:00Z', finished_at=f'2026-01-{3-i:02d}T00:0{i+1}:00Z')]}])
            for i in range(3)]
        group, = summarize_durations(SCOPE, rows)['groups']
        self.assertEqual((group['count'], group['average_seconds'], group['min_seconds'], group['max_seconds']),
                         (3, 120, 60, 180))
        self.assertEqual([s['run_id'] for s in group['samples']], ['run-3', 'run-2', 'run-1'])
        self.assertEqual(group['latest_seconds'], 60)

    def test_future_completion_is_not_observed_evidence(self):
        for end in ['2026-01-01T02:00:01Z', '2026-01-02T00:00:00+08:00']:
            result = summarize_durations(SCOPE, [run(stages=[{'name': 'build', 'jobs': [job(finished_at=end)]}])])
            self.assertEqual(result['groups'], [])
            self.assertEqual(result['coverage']['invalid_duration_jobs'], 1)
        result = summarize_durations(SCOPE, [run(stages=[{'name': 'build', 'jobs': [
            job(), job(id='job-2', finished_at='2026-01-01T02:00:01Z')]}])])
        self.assertEqual(result['groups'], [])
        self.assertEqual(result['coverage']['ambiguous_jobs'], 2)

    def test_completion_equal_to_observation_with_offset_is_valid(self):
        result = summarize_durations(SCOPE, [run(observed_at='2026-01-01T10:00:00+08:00',
            stages=[{'name': 'build', 'jobs': [job(finished_at='2026-01-01T02:00:00Z')]}])])
        self.assertEqual(result['groups'][0]['latest_seconds'], 7200)

    def test_malformed_structure_is_visible_not_exception(self):
        stages = [None, {'name': 'x', 'jobs': None}, {'name': 'build', 'jobs': [None, job()]}]
        result = summarize_durations(SCOPE, [run(stages=stages)])
        self.assertEqual(result['coverage']['malformed_entries'], 3)
        self.assertEqual(result['groups'][0]['count'], 1)

    def test_invalid_display_labels(self):
        for label in ['', ' leading', 'line\nbreak', 'a' * 257, True, {}]:
            result = summarize_durations(SCOPE, [run(stages=[{'name': label, 'jobs': [job()]}])])
            self.assertEqual(result['groups'], [])
            self.assertEqual(result['coverage']['invalid_duration_jobs'], 1)

    def test_output_is_allowlisted_and_input_not_mutated(self):
        rows = [run(extra='not-returned', stages=[{'name': '<label>', 'extra': 'not-returned',
                'jobs': [job(extra='not-returned')]}])]
        before = copy.deepcopy(rows)
        result = summarize_durations(SCOPE, rows)
        self.assertEqual(rows, before)
        self.assertNotIn('not-returned', json.dumps(result))
        self.assertEqual(result['groups'][0]['stage_name'], '<label>')  # consumer must escape text
        result['scope']['workspace_id'] = 'other'
        self.assertEqual(SCOPE.workspace_id, 'product-alpha')


if __name__ == '__main__':
    unittest.main()
