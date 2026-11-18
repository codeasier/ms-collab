"""Fictional provider projection for task UI. No network or real log payloads."""
from datetime import datetime, timezone
from packages.pipeline_sync import BoundCredential, Connection
from packages.pipeline_sync.provider import collect
from packages.pipelines import PipelineScope


class TaskProvider:
    def __init__(self, fractional_runs=False, *, unicode_labels=False):
        self.unicode_labels = unicode_labels
        self.fractional_ids = ('timing-01', 'timing-02', 'timing-03') if fractional_runs else ()

    def get(self,path,credential,connection):
        scope=connection.scope
        if '?limit=' in path:
            ids = (('run-tasks', 'unicode-labels') if self.unicode_labels else
                   ('run-tasks', 'run-missing', 'run-empty') + self.fractional_ids)
            return {'items':[{'id':key,'pipeline_id':scope.pipeline_id} for key in ids]}
        run_id=path.rsplit('/',1)[1]
        if self.unicode_labels and run_id=='unicode-labels':
            # Distinct final scalars expose prefix truncation. Failure is visible
            # in task/detail only, never as a successful duration observation.
            return {'pipeline_id':scope.pipeline_id,'id':run_id,'status':'failure','stages':[{
                'name':'\U00020000'*128+'\U00020001',
                'jobs':[{'id':'sample','name':'\U00020002'*255+'\U00020003','status':'success',
                         'started_at':'2026-01-01T10:00:00Z','finished_at':'2026-01-01T10:00:02Z'},
                        {'id':'detail-only','name':'d'*254+'\U00020004\U00020005','status':'failure',
                         'started_at':'2026-01-01T10:00:00Z','finished_at':'2026-01-01T10:00:03Z'}]}]}
        if run_id in self.fractional_ids:
            return {'pipeline_id':scope.pipeline_id, 'id':run_id, 'status':'success',
                    'stages':[{'name':'Timing', 'jobs':[{'id':'job-'+run_id, 'name':'Probe', 'status':'success',
                               'started_at':'2026-01-01T10:00:00Z', 'finished_at':'2026-01-01T10:00:00.100Z'}]}]}
        stages=None if run_id=='run-missing' else []
        if run_id=='run-tasks':
            jobs=[{'id':'duplicate' if i in (0,1,12) else f'job-{i}', 'name':f'Task {i:02d} <img src=x onerror=alert(1)>',
                   'status':'success' if i%2==0 else 'failure','started_at':'2026-01-01T10:00:00Z',
                   'finished_at':'2026-01-01T10:00:12Z','raw_log':'DISCARDED_SYNTHETIC_FIELD'} for i in range(23)]
            stages=[{'name':'<b>Build</b>','jobs':jobs[:12]},{'name':'<b>Build</b>','jobs':jobs[12:]},{'name':'Empty stage','jobs':[]}]
        return {'pipeline_id':scope.pipeline_id,'id':run_id,'status':'failure','stages':stages,
                'variables':{'fictional':'DISCARDED_SYNTHETIC_FIELD'},'logs':'DISCARDED_SYNTHETIC_FIELD'}


def snapshot(*, fractional_runs=False, unicode_labels=False):
    scope=PipelineScope('alpha','example-ci','build')
    connection=Connection(scope,'https://adapter.example.invalid',run_limit=2 if unicode_labels else 6 if fractional_runs else 5)
    return collect(connection,BoundCredential(scope,connection.origin,'synthetic-upstream-token-for-tests-only'),
        before_read=lambda:None,transport=TaskProvider(fractional_runs,unicode_labels=unicode_labels),clock=lambda:datetime(2026,1,2,tzinfo=timezone.utc))
