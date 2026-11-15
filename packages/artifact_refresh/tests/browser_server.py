"""Opt-in temporary HTTPS acceptance fixture. Never a deployed service or secret source."""
from datetime import datetime,timedelta,timezone
import json,os,signal,sqlite3,ssl,sys,tempfile,time
from pathlib import Path
from socketserver import ThreadingMixIn
from threading import Event
from urllib.parse import parse_qs,urlencode
from wsgiref.simple_server import WSGIServer,WSGIRequestHandler,make_server
from packages.application_ui import ApplicationUI
from packages.artifact_refresh.tests.fixtures import setup
from packages.artifact_sync.records import load as load_index, decode_snapshot
from packages.artifacts import Scope


def main():
    if os.environ.get('MS_COLLAB_SYNTHETIC_REFRESH_SESSION')!='1' or len(sys.argv)!=3:
        raise SystemExit('explicit synthetic opt-in and temporary certificate required')
    class Server(ThreadingMixIn,WSGIServer):daemon_threads=True
    class Handler(WSGIRequestHandler):
        def log_message(self,*args):pass
        def get_environ(self):result=super().get_environ();result['HTTPS']='on';return result
    with tempfile.TemporaryDirectory(prefix='ms-collab-refresh-session-') as directory:
        server=make_server('127.0.0.1',0,lambda e,s:[],server_class=Server,handler_class=Handler)
        origin='https://127.0.0.1:'+str(server.server_port);provider='https://localhost:'+str(server.server_port)+'/synthetic-provider'
        host,policy,login,exchange,pages=setup(directory,origin=origin,clock=lambda:datetime.now(timezone.utc),authorization_endpoint=provider)
        app=ApplicationUI(host=host);release=Event();mode=[''];offset=[0]
        host._artifact_admin._refresher._monotonic=lambda:time.monotonic()+offset[0]
        def page_hook():
            timestamp=(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat()
            pages.rows=(('builds/',timestamp,0),('builds/<svg onload=alert(1)>.txt',timestamp,12),('builds/synthetic-report.txt',timestamp,24))
            selected=mode[0];mode[0]=''
            if selected=='pause':release.wait(15)
            if selected=='timeout':offset[0]+=31
        pages.page_hook=page_hook
        def fixture(env,start):
            path=env.get('PATH_INFO','')
            if path.startswith('/synthetic/refresh/'):
                if env.get('REQUEST_METHOD')!='POST' or env.get('HTTP_ORIGIN')!=origin:
                    start('403 Forbidden',[('Content-Length','0')]);return [b'']
                action=path.rsplit('/',1)[-1];result={}
                if action=='state':
                    with sqlite3.connect(Path(directory)/'policy.sqlite') as db:
                        rows=db.execute('SELECT state FROM artifact_refresh_leases_v1 ORDER BY rowid').fetchall()
                        record=load_index(db,Scope('alpha','source','bucket'));published=record.published
                    result={'calls':len(pages.calls),'loads':len(pages.loads),'leases':[r[0] for r in rows],
                        'active':sum(r[0]=='active' for r in rows),'revision':record.revision,'pending':record.candidate is not None,
                        'published_rows':len(decode_snapshot(published,record.policy.connection).artifacts) if published is not None else 0}
                elif action=='pause':release.clear();mode[0]='pause'
                elif action=='release':release.set()
                elif action=='timeout':mode[0]='timeout'
                elif action=='bump':
                    with sqlite3.connect(Path(directory)/'policy.sqlite') as db:db.execute("UPDATE artifact_indexes_v1 SET revision=revision+1 WHERE workspace='alpha'")
                elif action=='revoke':policy.set_membership('second','alpha','admin',role='admin',state='revoked',expected_revision=1)
                else:start('404 Not Found',[('Content-Length','0')]);return [b'']
                raw=json.dumps(result).encode();start('200 OK',[('Content-Type','application/json'),('Content-Length',str(len(raw))),('Cache-Control','no-store')]);return [raw]
            if path=='/synthetic-provider':
                params=parse_qs(env.get('QUERY_STRING',''),strict_parsing=True)
                if env.get('HTTP_HOST')!='localhost:'+str(server.server_port) or params.get('code_challenge_method')!=['S256'] or params.get('redirect_uri')!=[login.binding.callback_uri] or params.get('client_id')!=[login.binding.audience]:
                    start('400 Bad Request',[('Content-Length','0')]);return [b'']
                code=exchange.authorize(params['code_challenge'][0]);target=login.binding.callback_uri+'?'+urlencode({'code':code,'state':params['state'][0]})
                start('303 See Other',[('Location',target),('Content-Length','0'),('Cache-Control','no-store'),('Referrer-Policy','no-referrer')]);return [b'']
            return app(env,start)
        server.set_app(fixture);context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(sys.argv[1],sys.argv[2]);server.socket=context.wrap_socket(server.socket,server_side=True)
        signal.signal(signal.SIGTERM,lambda *_:(_ for _ in ()).throw(SystemExit()))
        print(json.dumps({'origin':origin,'provider':provider}),flush=True)
        try:server.serve_forever()
        finally:release.set();server.server_close()

if __name__=='__main__':main()
