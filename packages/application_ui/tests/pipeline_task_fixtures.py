"""Fictional provider projection for task UI. No network or real log payloads."""
from datetime import datetime, timezone
from packages.pipeline_sync import BoundCredential, Connection
from packages.pipeline_sync.provider import collect
from packages.pipelines import PipelineScope


class TaskProvider:
    def get(self,path,credential,connection):
        scope=connection.scope
        if '?limit=' in path:
            return {'items':[{'id':key,'pipeline_id':scope.pipeline_id} for key in ('run-tasks','run-missing','run-empty')]}
        run_id=path.rsplit('/',1)[1]
        stages=None if run_id=='run-missing' else []
        if run_id=='run-tasks':
            jobs=[{'id':'duplicate' if i in (0,1,12) else f'job-{i}', 'name':f'Task {i:02d} <img src=x onerror=alert(1)>',
                   'status':'success' if i%2==0 else 'failure','started_at':'2026-01-01T10:00:00Z',
                   'finished_at':'2026-01-01T10:00:12Z','raw_log':'DISCARDED_SYNTHETIC_FIELD'} for i in range(23)]
            stages=[{'name':'<b>Build</b>','jobs':jobs[:12]},{'name':'<b>Build</b>','jobs':jobs[12:]},{'name':'Empty stage','jobs':[]}]
        return {'pipeline_id':scope.pipeline_id,'id':run_id,'status':'failure','stages':stages,
                'variables':{'fictional':'DISCARDED_SYNTHETIC_FIELD'},'logs':'DISCARDED_SYNTHETIC_FIELD'}


def snapshot():
    scope=PipelineScope('alpha','example-ci','build')
    connection=Connection(scope,'https://adapter.example.invalid')
    return collect(connection,BoundCredential(scope,connection.origin,'synthetic-upstream-token-for-tests-only'),
        before_read=lambda:None,transport=TaskProvider(),clock=lambda:datetime(2026,1,2,tzinfo=timezone.utc))
