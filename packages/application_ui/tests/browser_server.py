"""Opt-in temporary HTTPS fixture. Synthetic control routes are NOT product APIs."""
import json
import os
from pathlib import Path
import signal
import sqlite3
import ssl
import sys
import tempfile
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlencode
from wsgiref.simple_server import make_server, WSGIRequestHandler

from packages.application_host.tests.fixtures import setup
from packages.application_ui import ApplicationUI


def main():
    if os.environ.get('MS_COLLAB_SYNTHETIC_APPLICATION_UI') != '1' or len(sys.argv) not in (3,4):
        raise SystemExit('explicit synthetic opt-in and temporary certificate required')
    mode=sys.argv[3] if len(sys.argv)==4 else ''
    if mode not in {'','closed','pipelines','assets','pipeline-tasks'}:raise SystemExit('unknown synthetic mode')
    enabled=mode!='closed'
    class QuietHTTPS(WSGIRequestHandler):
        def log_message(self,*args):pass
        def get_environ(self):
            env=super().get_environ();env['HTTPS']='on';return env
    with tempfile.TemporaryDirectory(prefix='ms-collab-application-ui-') as directory:
        server=make_server('127.0.0.1',0,lambda e,s:[],handler_class=QuietHTTPS)
        origin='https://127.0.0.1:'+str(server.server_port)
        provider='https://localhost:'+str(server.server_port)+'/synthetic-provider'
        host,policy,login,exchange=setup(directory,origin=origin,enabled=enabled,
            authorization_endpoint=provider,clock=lambda:datetime.now(timezone.utc))
        app=ApplicationUI(host=host)
        with sqlite3.connect(Path(directory)/'policy.sqlite') as db:
            db.execute("UPDATE workspaces SET name=? WHERE id='alpha'",('<img src=x onerror=alert(1)> Synthetic alpha',))
        if mode=='pipelines':
            from packages.pipeline_api import encode_snapshots,resource_id
            from packages.pipeline_api.tests.test_application import run
            from packages.pipelines import PipelineScope
            scope=PipelineScope('alpha','example-ci','build')
            rows=[]
            for n in range(1,21):
                row=run(scope,id=f'run-{n:02d}',state='running' if n==19 else 'failure')
                row['stages'][0]['name']='<b>Checks</b>'
                row['stages'][0]['jobs'][0]['name']='<img src=x onerror=alert(1)>'
                if n==19:row['stages']=None
                rows.append(row)
            with sqlite3.connect(Path(directory)/'policy.sqlite') as db:
                db.execute("UPDATE resources SET text=? WHERE workspace_id='alpha' AND module_key='pipelines' AND id=?",(encode_snapshots(scope,rows),resource_id(scope)))
        if mode=='pipeline-tasks':
            from .pipeline_task_fixtures import snapshot
            from packages.pipeline_api import resource_id
            from packages.pipelines import PipelineScope
            with sqlite3.connect(Path(directory)/'policy.sqlite') as db:
                db.execute("UPDATE resources SET text=? WHERE workspace_id='alpha' AND module_key='pipelines' AND id=?",(snapshot(),resource_id(PipelineScope('alpha','example-ci','build'))))
        if mode=='assets':
            from .research_asset_fixtures import provision
            provision(policy)
        def fixture(env,start):
            path=env.get('PATH_INFO')
            if path=='/synthetic/advance-pipeline-tasks' and mode=='pipeline-tasks':
                if env.get('REQUEST_METHOD')!='POST' or env.get('HTTP_ORIGIN')!=origin:
                    start('403 Forbidden',[('Content-Length','0')]);return [b'']
                from packages.pipeline_api import resource_id
                from packages.pipelines import PipelineScope
                with sqlite3.connect(Path(directory)/'policy.sqlite') as db:
                    db.execute("UPDATE resources SET revision=revision+1 WHERE workspace_id='alpha' AND module_key='pipelines' AND id=?",(resource_id(PipelineScope('alpha','example-ci','build')),))
                start('200 OK',[('Content-Length','0')]);return [b'']
            if path=='/synthetic/advance-assets' and mode=='assets':
                if env.get('REQUEST_METHOD')!='POST' or env.get('HTTP_ORIGIN')!=origin:
                    start('403 Forbidden',[('Content-Length','0')]);return [b'']
                with sqlite3.connect(Path(directory)/'policy.sqlite') as db:
                    db.execute("UPDATE research_catalog SET revision=revision+1 WHERE workspace_id='alpha' AND catalog_id='knowledge'")
                start('200 OK',[('Content-Length','0')]);return [b'']
            if path in {'/synthetic/revoke-membership','/synthetic/revoke-session'}:
                if env.get('REQUEST_METHOD')!='POST' or env.get('HTTP_ORIGIN')!=origin:
                    start('403 Forbidden',[('Content-Length','0')]);return [b'']
                if path.endswith('membership'):policy.set_membership('second','alpha','admin',role='admin',state='revoked',expected_revision=1)
                else:login.revoke_subject('admin-sub')
                start('200 OK',[('Content-Length','0'),('Cache-Control','no-store')]);return [b'']
            if path=='/synthetic-provider' and enabled:
                params=parse_qs(env.get('QUERY_STRING',''),strict_parsing=True)
                if (env.get('HTTP_HOST')!='localhost:'+str(server.server_port)
                        or params.get('code_challenge_method')!=['S256']
                        or params.get('redirect_uri')!=[login.binding.callback_uri]
                        or params.get('client_id')!=[login.binding.audience]):
                    start('400 Bad Request',[('Content-Length','0')]);return [b'']
                code=exchange.authorize(params['code_challenge'][0])
                target=login.binding.callback_uri+'?'+urlencode({'code':code,'state':params['state'][0]})
                start('303 See Other',[('Location',target),('Content-Length','0'),('Cache-Control','no-store'),('Referrer-Policy','no-referrer')]);return [b'']
            return app(env,start)
        server.set_app(fixture)
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(sys.argv[1],sys.argv[2]);server.socket=context.wrap_socket(server.socket,server_side=True)
        signal.signal(signal.SIGTERM,lambda *_:(_ for _ in ()).throw(SystemExit()))
        print(json.dumps({'origin':origin,'provider':provider}),flush=True)
        try:server.serve_forever()
        finally:server.server_close()

if __name__=='__main__':main()
