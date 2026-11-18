"""Opt-in synthetic HTTPS cookie/byte acceptance, never a production listener."""
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import signal
import sqlite3
import ssl
import sys
import tempfile
from threading import Event
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs,urlencode
from wsgiref.simple_server import WSGIServer,WSGIRequestHandler,make_server
from dataclasses import replace
from packages.application_ui import ApplicationUI
from packages.artifact_download.tests.session_fixtures import setup_download,KEY,DATA,BINDING,GRANT
from packages.artifact_download import records
from packages.artifact_sync.tests.authority_fixtures import configure_bytes_fixture


def main():
    wheel=(len(sys.argv)==4 and sys.argv[3]=='--wheel')
    product=(len(sys.argv)==4 and sys.argv[3]=='--product')
    if os.environ.get('MS_COLLAB_SYNTHETIC_DOWNLOAD_SESSION')!='1' or (len(sys.argv)!=3 and not (wheel or product)):raise SystemExit('explicit fixture opt-in and temporary certificate required')
    class Server(ThreadingMixIn,WSGIServer):daemon_threads=True
    class Handler(WSGIRequestHandler):
        def log_message(self,*args):pass
        def get_environ(self):result=super().get_environ();result['HTTPS']='on';return result
    with tempfile.TemporaryDirectory(prefix='ms-collab-download-session-') as directory:
        server=make_server('127.0.0.1',0,lambda e,s:[],server_class=Server,handler_class=Handler)
        origin='https://127.0.0.1:'+str(server.server_port);provider='https://localhost:'+str(server.server_port)+'/synthetic-provider'
        if product:
            from packages.artifact_download.tests.product_file_fixtures import setup_product_download, republish_product_file
            host,policy,login,exchange,objects=setup_product_download(directory,origin=origin,clock=lambda:datetime.now(timezone.utc),authorization_endpoint=provider)
        else:
            host,policy,login,exchange,objects=setup_download(directory,origin=origin,clock=lambda:datetime.now(timezone.utc),authorization_endpoint=provider,browser_wheel=wheel)
        if not (wheel or product):
            from packages.application_ui.tests.artifact_discovery_fixtures import seed_directory
            seed_directory(policy)
        app=ApplicationUI(host=host, product_file_downloads=True) if product else ApplicationUI(host=host)
        release=Event();paused=[False]
        def pause_read():
            if paused[0]:paused[0]=False;release.wait(15)
        objects.read_hook=pause_read
        def fixture(env,start):
            path=env.get('PATH_INFO','')
            if path.startswith('/synthetic/download/'):
                if env.get('REQUEST_METHOD')!='POST' or env.get('HTTP_ORIGIN')!=origin:
                    start('403 Forbidden',[('Content-Length','0')]);return [b'']
                if wheel and path!='/synthetic/download/state':
                    start('404 Not Found',[('Content-Length','0')]);return [b'']
                if product and (path not in {'/synthetic/download/state','/synthetic/download/product-republish',
                                            '/synthetic/download/product-revoke-session'}
                                or env.get('HTTP_HOST')!='127.0.0.1:'+str(server.server_port)
                                or env.get('QUERY_STRING') or env.get('CONTENT_LENGTH','') not in ('','0')):
                    start('404 Not Found',[('Content-Length','0')]);return [b'']
                action=path.rsplit('/',1)[-1];result={}
                if action=='state':
                    with sqlite3.connect(Path(directory)/'policy.sqlite') as db:
                        rows=db.execute('SELECT state,offered FROM artifact_download_leases_v1 ORDER BY rowid').fetchall()
                    result={'calls':len(objects.calls),'loads':len(objects.loads),'leases':rows,'active':sum(r[0]=='active' for r in rows)}
                elif product and action=='product-republish':
                    result={'published_revision':republish_product_file(policy)}
                elif product and action=='product-revoke-session':
                    # One hardcoded synthetic subject; no identity or cookie in the request.
                    login.revoke_subject('admin-sub')
                elif action=='rotate-binding':
                    configure_bytes_fixture(policy,replace(GRANT,bindings=(replace(BINDING,sha256='a'*64),)))
                elif action=='reset':
                    release.set();paused[0]=False;objects.data=DATA
                    configure_bytes_fixture(policy,GRANT)
                elif action=='pause-acquire':release.clear();paused[0]=True
                elif action=='release-acquire':release.set()
                elif action=='revoke-member':policy.set_membership('second','alpha','admin',role='admin',state='revoked',expected_revision=1)
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
        info={'origin':origin,'provider':provider}
        if not (wheel or product):info.update(key=KEY,sha256=BINDING.sha256,size=len(DATA))
        print(json.dumps(info),flush=True)
        try:server.serve_forever()
        finally:release.set();server.server_close()

if __name__=='__main__':main()
