"""Pure revision-local task projection; never provider/job execution authority."""
from collections import Counter

STATES = ('success', 'failure', 'cancelled', 'timed_out', 'skipped', 'queued', 'running', 'unknown')


def _counts(jobs):
    counts = Counter(job['status'] for job in jobs)
    return {status: counts[status] for status in STATES}


def task_page(row, *, offset=0, limit=20, status=''):
    """Input is the codec-validated run. Positions are only stable in its revision.

    Preserve duplicate job IDs, including across stages, as distinct observations.
    Do not infer stage success, parent-run success or a remotely addressable job.
    """
    stages = row['stages'] or []
    jobs = [job for stage in stages for job in stage['jobs']]
    occurrences = Counter(job['id'] for job in jobs)
    items = [dict(stage_index=si, job_index=ji, **job, id_occurrences=occurrences[job['id']])
             for si, stage in enumerate(stages) for ji, job in enumerate(stage['jobs'])
             if not status or job['status'] == status]
    return {
        'run': {key: row[key] for key in ('id', 'status', 'observed_at')} | {
            'details_available': row['stages'] is not None},
        'coverage': {'stage_count': len(stages), 'task_count': len(jobs),
                     'status_counts': _counts(jobs),
                     'duplicate_id_tasks': sum(occurrences[job['id']] > 1 for job in jobs),
                     'stage_status_supplied': False},
        'stages': [{'index': si, 'name': stage['name'], 'task_count': len(stage['jobs']),
                    'status_counts': _counts(stage['jobs'])} for si, stage in enumerate(stages)],
        'filter': {'status': status}, 'total': len(items), 'offset': offset, 'limit': limit,
        'items': items[offset:offset + limit],
    }
