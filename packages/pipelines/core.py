"""Scoped, evidence-only duration trends; no provider, storage or execution access.

Adapted from the MIT-licensed OBS pipeline_durations grouping algorithm.
Copyright (c) 2026 codeasier. See MIGRATION.md for scope and changes.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
from statistics import mean

MAX_SNAPSHOTS = 5000
MAX_JOBS = 10000
TERMINAL = frozenset({'success', 'failure', 'cancelled', 'timed_out', 'skipped'})
_ALIASES = {
    'success': 'success', 'succeeded': 'success', 'passed': 'success',
    'failure': 'failure', 'failed': 'failure', 'error': 'failure',
    'canceled': 'cancelled', 'cancelled': 'cancelled', 'aborted': 'cancelled',
    'timeout': 'timed_out', 'timedout': 'timed_out', 'timed_out': 'timed_out',
    'skipped': 'skipped', 'queued': 'queued', 'pending': 'queued',
    'running': 'running', 'in_progress': 'running', 'unknown': 'unknown',
}


class InvalidSnapshot(ValueError):
    """Stable reason only; never echoes input data into exceptions."""


def normalize_status(status, conclusion=None):
    """A completed phase alone is not success. Unknown/conflicting facts stay unknown.

    This is a small explicit vocabulary, not an implicit adapter for every provider.
    Providers with other status meanings must translate them before calling this API.
    """
    if not isinstance(status, str):
        return 'unknown'
    value = status.strip().lower()
    state = _ALIASES.get(value, 'unknown')
    if conclusion is None:
        return 'unknown' if value == 'completed' else state
    if not isinstance(conclusion, str):
        return 'unknown'
    outcome = _ALIASES.get(conclusion.strip().lower(), 'unknown')
    if outcome not in TERMINAL:
        return 'unknown'
    return outcome if value == 'completed' or state == outcome else 'unknown'


def _identifier(value):
    return (isinstance(value, str)
            and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', value) is not None)


def _label(value):
    return (isinstance(value, str) and 0 < len(value) <= 256
            and value == value.strip() and all(c.isprintable() for c in value))


@dataclass(frozen=True)
class PipelineScope:
    workspace_id: str
    connection_id: str
    pipeline_id: str

    def __post_init__(self):
        if not all(_identifier(v) for v in (self.workspace_id, self.connection_id, self.pipeline_id)):
            raise InvalidSnapshot('invalid_scope')

    def as_dict(self):
        return {k: getattr(self, k) for k in ('workspace_id', 'connection_id', 'pipeline_id')}


def timestamp(value):
    """An explicit-timezone ISO datetime, never inferred locale or epoch units."""
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if result.tzinfo is None or result.utcoffset() is None:
            return None
        return result.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def summarize_durations(scope, snapshots, *, limit=30):
    """Summarize a bounded supplied snapshot set after caller authorization.

    Select newest observed terminal runs before aggregating successful jobs.
    Reject scope mismatches and duplicate run IDs, rather than combine datasets.
    This function is not a permission gate or an authoritative history query.
    """
    if type(scope) is not PipelineScope:
        raise InvalidSnapshot('invalid_scope')
    if type(limit) is not int or not 1 <= limit <= 500:
        raise InvalidSnapshot('invalid_limit')
    if type(snapshots) not in (list, tuple) or len(snapshots) > MAX_SNAPSHOTS:
        raise InvalidSnapshot('invalid_snapshot_set')
    rows, ids, total_jobs = [], set(), 0
    for row in snapshots:
        if not isinstance(row, dict):
            raise InvalidSnapshot('invalid_snapshot')
        if any(row.get(key) != value for key, value in scope.as_dict().items()):
            raise InvalidSnapshot('scope_mismatch')
        run_id = row.get('id')
        if not _identifier(run_id):
            raise InvalidSnapshot('invalid_run_id')
        if run_id in ids:
            raise InvalidSnapshot('duplicate_run_id')
        ids.add(run_id)
        observed = timestamp(row.get('observed_at'))
        if observed is None:
            raise InvalidSnapshot('invalid_observation_time')
        stages = row.get('stages')
        if isinstance(stages, list):
            if len(stages) > 200:
                raise InvalidSnapshot('too_many_stages')
            for stage in stages:
                if isinstance(stage, dict) and isinstance(stage.get('jobs'), list):
                    total_jobs += len(stage['jobs'])
                    if total_jobs > MAX_JOBS:
                        raise InvalidSnapshot('too_many_jobs')
        rows.append((observed, run_id, row, normalize_status(row.get('status'), row.get('conclusion'))))

    terminal = sorted((r for r in rows if r[3] in TERMINAL), key=lambda r: (r[0], r[1]), reverse=True)
    selected = terminal[:limit]
    coverage = {
        'source': 'supplied_snapshots', 'full_history_complete': False,
        'limit': limit, 'supplied_runs': len(rows),
        'nonterminal_or_unknown_runs': len(rows) - len(terminal),
        'observed_terminal_runs': len(terminal), 'selected_runs': len(selected),
        'selection_truncated': len(terminal) > len(selected), 'runs_with_details': 0,
        'runs_with_samples': 0, 'missing_details': 0, 'malformed_entries': 0,
        'excluded_jobs': 0, 'invalid_duration_jobs': 0, 'ambiguous_jobs': 0,
    }
    groups = defaultdict(list)
    for observed, run_id, row, state in selected:
        stages = row.get('stages')
        if not isinstance(stages, list) or not stages:
            coverage['missing_details'] += 1
            continue
        coverage['runs_with_details'] += 1
        candidates = defaultdict(list)
        jobs = [job for stage in stages if isinstance(stage, dict) and isinstance(stage.get('jobs'), list)
                for job in stage['jobs'] if isinstance(job, dict)]
        id_counts = Counter(job['id'] for job in jobs if _identifier(job.get('id')))
        for stage in stages:
            if not isinstance(stage, dict) or not isinstance(stage.get('jobs'), list):
                coverage['malformed_entries'] += 1
                continue
            for job in stage['jobs']:
                if not isinstance(job, dict):
                    coverage['malformed_entries'] += 1
                    continue
                if normalize_status(job.get('status'), job.get('conclusion')) != 'success':
                    coverage['excluded_jobs'] += 1
                    continue
                stage_name, job_name = stage.get('name'), job.get('name')
                if not _label(stage_name) or not _label(job_name):
                    coverage['invalid_duration_jobs'] += 1
                    continue
                key = (stage_name, job_name)
                start, end = timestamp(job.get('started_at')), timestamp(job.get('finished_at'))
                job_id = job.get('id')
                if (start is None or end is None or end < start or end > observed or not _identifier(job_id)
                        or id_counts[job_id] != 1):
                    coverage['invalid_duration_jobs'] += 1
                    # Missing evidence must not make an otherwise ambiguous name unique.
                    candidates[key].append(None)
                    continue
                candidates[key].append({
                    'run_id': run_id, 'run_status': state, 'job_id': job_id,
                    'observed_at': observed.isoformat(),
                    'started_at': start.isoformat(), 'finished_at': end.isoformat(),
                    'duration_seconds': (end - start).total_seconds(),
                })
        included = False
        for key, samples in candidates.items():
            if len(samples) != 1:
                coverage['ambiguous_jobs'] += len(samples)
                continue
            if samples[0] is not None:
                groups[key].append(samples[0])
                included = True
        coverage['runs_with_samples'] += int(included)
    result = []
    for (stage_name, job_name), samples in sorted(groups.items()):
        samples.sort(key=lambda s: (s['started_at'], s['run_id']))
        durations = [s['duration_seconds'] for s in samples]
        result.append({
            'key': json.dumps([stage_name, job_name], ensure_ascii=False),
            'stage_name': stage_name, 'job_name': job_name,
            'count': len(samples), 'average_seconds': mean(durations),
            'min_seconds': min(durations), 'max_seconds': max(durations),
            'latest_seconds': durations[-1], 'samples': samples,
        })
    return {'scope': scope.as_dict(), 'groups': result, 'coverage': coverage}
