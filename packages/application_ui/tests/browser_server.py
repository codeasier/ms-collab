"""Opt-in temporary HTTPS fixture. Synthetic control routes are NOT product APIs."""
import json
import os
from pathlib import Path
import sqlite3
import ssl
import sys
import tempfile
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlencode, urlsplit
from wsgiref.simple_server import make_server, WSGIRequestHandler

from packages.application_host.tests.fixtures import setup
from packages.application_ui import ApplicationUI
from packages.application_ui.tests.synthetic_shutdown import serve_synthetic


TRANSFER_MODE = 'admin-transfer'
TRANSFER_PREFIX = '/synthetic/admin-transfer/'
TRANSFER_CONTROLS = (TRANSFER_PREFIX + 'status', TRANSFER_PREFIX + 'repromote')


def dispatch_admin_transfer(policy, *, mode, origin, environ, start_response):
    """Fixed disposable-fixture controls, not recovery or product endpoints.

    No scope, target, identity, SQL, key or proof is accepted from the caller.
    The re-promotion is a separate legitimate policy edit by the new admin. It
    only sets up historical replay acceptance; it is never recommended recovery.
    """
    path = environ.get('PATH_INFO', '')
    if mode != TRANSFER_MODE or type(path) is not str or not path.startswith(TRANSFER_PREFIX):
        return None

    def respond(status, value=None):
        raw = b'' if value is None else json.dumps(value, ensure_ascii=True, separators=(',', ':')).encode('ascii')
        start_response(status, [('Content-Type', 'text/plain' if value is None else 'application/json'),
            ('Content-Length', str(len(raw))), ('Cache-Control', 'no-store')])
        return [raw]

    if (environ.get('REQUEST_METHOD') != 'POST'
            or environ.get('HTTP_ORIGIN') != origin
            or environ.get('wsgi.url_scheme') != 'https'
            or environ.get('HTTP_HOST') != urlsplit(origin).netloc
            or environ.get('HTTP_SEC_FETCH_SITE') == 'cross-site'):
        return respond('403 Forbidden')
    if path not in TRANSFER_CONTROLS:
        return respond('404 Not Found')
    if (environ.get('QUERY_STRING', '') != ''
            or environ.get('SCRIPT_NAME', '') != ''
            or environ.get('CONTENT_LENGTH', '') not in ('', '0')
            or environ.get('CONTENT_TYPE', '') not in ('', 'text/plain')
            or 'HTTP_TRANSFER_ENCODING' in environ
            or any(name in environ for name in ('HTTP_AUTHORIZATION', 'HTTP_X_WORKSPACE_ID',
                'HTTP_X_PRINCIPAL_ID', 'HTTP_X_USER_ID', 'HTTP_X_SUBJECT', 'REMOTE_USER'))):
        return respond('400 Bad Request')
    from packages.workspace_admin_api.tests.transfer_fixtures import BROWSER_ACTOR
    from packages.workspaces import PolicyError

    def snapshot(current, db):
        actor = current._memberships[('alpha', BROWSER_ACTOR)]
        reader = current._memberships[('alpha', 'reader')]
        return {'actor_role': actor.role, 'actor_revision': actor.revision,
            'replacement_role': reader.role, 'replacement_revision': reader.revision,
            'receipts': db.execute('SELECT count(*) FROM workspace_admin_transfers_v1').fetchone()[0],
            'transfer_audits': db.execute("SELECT count(*) FROM audit WHERE action IN ('membership.promote','membership.demote')").fetchone()[0],
            'audits': len(current._audit), 'epoch': current._epoch}

    def repromote(current):
        actor = current._memberships[('alpha', BROWSER_ACTOR)]
        reader = current._memberships[('alpha', 'reader')]
        if (actor.role, actor.state, actor.revision, reader.role, reader.state, reader.revision) != (
                'developer', 'active', 2, 'admin', 'active', 2):
            raise PolicyError(409, 'synthetic_fixture_conflict')
        current.set_membership('reader', 'alpha', BROWSER_ACTOR,
            role='admin', state='active', expected_revision=2)

    try:
        if path == TRANSFER_PREFIX + 'repromote':
            policy._transaction(repromote, write=True)
        value = policy._transaction(snapshot, write=False, with_connection=True)
    except PolicyError:
        return respond('409 Conflict')
    return respond('200 OK', value)


def main():
    if os.environ.get('MS_COLLAB_SYNTHETIC_APPLICATION_UI') != '1' or len(sys.argv) not in (3,4):
        raise SystemExit('explicit synthetic opt-in and temporary certificate required')
    mode=sys.argv[3] if len(sys.argv)==4 else ''
    if mode not in {'','closed','pipelines','assets','pipeline-tasks','pipeline-discovery','module-status',TRANSFER_MODE}:raise SystemExit('unknown synthetic mode')
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
            authorization_endpoint=provider,clock=lambda:datetime.now(timezone.utc),
            **({'admin_transfers': True} if mode == TRANSFER_MODE else {}))
        if mode=='module-status':
            from .module_status_fixture import dispatch,provision
            provision(policy)
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
        ci_calls=[]
        if mode=='pipeline-discovery':
            from .pipeline_discovery_fixtures import seed_directory
            seed_directory(policy)
            # Any accidental reuse of upstream synchronization is a fixture
            # failure, not a provider mock returning convenient data.
            from packages.pipeline_sync import service as pipeline_sync
            def forbidden_provider(*args, **kwargs):
                ci_calls.append('unexpected'); raise AssertionError('no provider calls allowed')
            pipeline_sync.collect=forbidden_provider
        if mode=='assets':
            from .research_asset_fixtures import provision
            provision(policy)
        def fixture(env,start):
            path=env.get('PATH_INFO')
            if mode == TRANSFER_MODE:
                response = dispatch_admin_transfer(policy, mode=mode, origin=origin,
                    environ=env, start_response=start)
                if response is not None:return response
            if mode=='module-status':
                response=dispatch(policy,mode=mode,origin=origin,environ=env,start_response=start)
                if response is not None:return response
            if path.startswith('/synthetic/pipeline/') and mode=='pipeline-discovery':
                if env.get('REQUEST_METHOD')!='POST' or env.get('HTTP_ORIGIN')!=origin:
                    start('403 Forbidden',[('Content-Length','0')]);return [b'']
                from .pipeline_discovery_fixtures import seed_pages
                from packages.pipeline_api import resource_id
                from packages.pipelines import PipelineScope
                if path.endswith('/seed-pages'):seed_pages(policy)
                elif path.endswith('/invalidate-cursor'):
                    with sqlite3.connect(Path(directory)/'policy.sqlite') as db:
                        db.execute("DELETE FROM resources WHERE workspace_id='alpha' AND module_key='pipelines' AND id=?",(resource_id(PipelineScope('alpha','example-ci','p022')),))
                elif not path.endswith('/status'):
                    start('404 Not Found',[('Content-Length','0')]);return [b'']
                raw=json.dumps({'ci_calls':len(ci_calls)}).encode()
                start('200 OK',[('Content-Length',str(len(raw))),('Content-Type','application/json'),('Cache-Control','no-store')]);return [raw]
            if path=='/synthetic/advance-pipeline-tasks' and mode=='pipeline-tasks':
                if env.get('REQUEST_METHOD')!='POST' or env.get('HTTP_ORIGIN')!=origin:
                    start('403 Forbidden',[('Content-Length','0')]);return [b'']
                from packages.pipeline_api import resource_id
                from packages.pipelines import PipelineScope
                with sqlite3.connect(Path(directory)/'policy.sqlite') as db:
                    # Keep revision one (and its screenshots) unchanged. Publish
                    # three equal subsecond observations with the revision bump.
                    db.execute("UPDATE resources SET text=?, revision=revision+1 WHERE workspace_id='alpha' AND module_key='pipelines' AND id=?",
                               (snapshot(fractional_runs=True), resource_id(PipelineScope('alpha','example-ci','build'))))
                start('200 OK',[('Content-Length','0')]);return [b'']
            if path=='/synthetic/advance-assets' and mode=='assets':
                if env.get('REQUEST_METHOD')!='POST' or env.get('HTTP_ORIGIN')!=origin:
                    start('403 Forbidden',[('Content-Length','0')]);return [b'']
                with sqlite3.connect(Path(directory)/'policy.sqlite') as db:
                    db.execute("UPDATE research_catalog SET revision=revision+1 WHERE workspace_id='alpha' AND catalog_id='knowledge'")
                start('200 OK',[('Content-Length','0')]);return [b'']
            if path in {'/synthetic/revoke-membership','/synthetic/revoke-session'} and mode != TRANSFER_MODE:
                if env.get('REQUEST_METHOD')!='POST' or env.get('HTTP_ORIGIN')!=origin:
                    start('403 Forbidden',[('Content-Length','0')]);return [b'']
                if path.endswith('membership'):policy.set_membership('second','alpha','admin',role='admin',state='revoked',expected_revision=1)
                else:login.revoke_subject('admin-sub')
                start('200 OK',[('Content-Length','0'),('Cache-Control','no-store')]);return [b'']
            if (path == '/synthetic-provider' or (mode == TRANSFER_MODE and path == '/synthetic-provider-reader')) and enabled:
                if mode == TRANSFER_MODE:
                    try:
                        params = parse_qs(env.get('QUERY_STRING', ''), strict_parsing=True)
                    except ValueError:
                        start('400 Bad Request', [('Content-Length', '0')]);return [b'']
                    if (env.get('REQUEST_METHOD') != 'GET' or env.get('wsgi.url_scheme') != 'https'
                            or set(params) != {'response_type', 'client_id', 'redirect_uri', 'scope',
                                               'state', 'code_challenge', 'code_challenge_method'}
                            or any(len(value) != 1 or not value[0] for value in params.values())
                            or params.get('response_type') != ['code']
                            or params.get('scope') != ['synthetic_user']):
                        start('400 Bad Request', [('Content-Length', '0')]);return [b'']
                else:
                    params=parse_qs(env.get('QUERY_STRING',''),strict_parsing=True)
                if (env.get('HTTP_HOST')!='localhost:'+str(server.server_port)
                        or params.get('code_challenge_method')!=['S256']
                        or params.get('redirect_uri')!=[login.binding.callback_uri]
                        or params.get('client_id')!=[login.binding.audience]):
                    start('400 Bad Request',[('Content-Length','0')]);return [b'']
                subject = 'reader-sub' if mode=='module-status' else 'admin-sub'
                if mode == TRANSFER_MODE:
                    from packages.workspace_admin_api.tests.transfer_fixtures import BROWSER_SUBJECT
                    subject = 'reader-sub' if path == '/synthetic-provider-reader' else BROWSER_SUBJECT
                code=exchange.authorize(params['code_challenge'][0], subject)
                target=login.binding.callback_uri+'?'+urlencode({'code':code,'state':params['state'][0]})
                start('303 See Other',[('Location',target),('Content-Length','0'),('Cache-Control','no-store'),('Referrer-Policy','no-referrer')]);return [b'']
            return app(env,start)
        server.set_app(fixture)
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(sys.argv[1],sys.argv[2]);server.socket=context.wrap_socket(server.socket,server_side=True)
        serve_synthetic(server, on_ready=lambda: print(
            json.dumps({'origin':origin,'provider':provider}),flush=True))

if __name__=='__main__':main()
