"""Isolated synthetic verifier + real configured API; never a deployable server."""
from datetime import timedelta
import os
from .reference_fixture import identity_aba, membership_change
from packages.application_ui.tests.synthetic_shutdown import serve_synthetic
from wsgiref.simple_server import make_server,WSGIRequestHandler
from packages.community_board.model import Snapshot,BoardItem
from packages.community_api.tests.test_application import call
from packages.community_api.records import fingerprint
from packages.responsibility import normalize_paths
from packages.responsibility_api.tests.test_application import ResponsibilityTests,SCOPE,DIFF
from packages.responsibility_ui import ResponsibilityUI

class Quiet(WSGIRequestHandler):
    def log_message(self,*args):pass

def main():
    fixture=ResponsibilityTests();fixture.setUp()
    try:
        fixture.provider.observe=lambda connection,credential,observed_at,before_read=None:Snapshot(connection.scope,observed_at,tuple(
            [BoardItem('issue',1,'<img src=x onerror=alert(1)> Synthetic issue','open','2026-01-01T01:00:00Z')]+
            [BoardItem('pull',n,'Synthetic UI change '+str(n),'open','2026-01-01T01:00:00Z') for n in range(2,34)]))
        fixture.now+=timedelta(seconds=1)
        assert call(fixture.community,'/refresh',method='POST',token='test-admin',revision=3,body={})['status']==200
        assert call(fixture.community,'/approve',method='POST',token='test-admin',revision=4,body={'fingerprints':[fingerprint(SCOPE,i) for i in fixture.cstate().snapshot.items if i.number!=33]})['status']==200
        assert fixture.save()['status']==200
        observations=[{'fingerprint':fingerprint(SCOPE,i),'paths':normalize_paths(DIFF,[[{'filename':'ui/file-'+str(i.number),'status':'modified'}]],DIFF).as_dict()} for i in fixture.cstate().snapshot.items if i.kind=='pull' and i.number not in (32,33)]
        fixture.publish(observations)
        with make_server('127.0.0.1',0,None,handler_class=Quiet) as server:
            origin=f'http://127.0.0.1:{server.server_port}'
            app=ResponsibilityUI(api=fixture.app,origin=origin)
            def synthetic(env,start):
                path=env.get('PATH_INFO')
                if os.environ.get('MS_COLLAB_SYNTHETIC_RESPONSIBILITY_UI')=='1' and path in {'/synthetic/identity-aba','/synthetic/revoke-target','/synthetic/restore-target'}:
                    if env.get('REQUEST_METHOD')!='POST' or env.get('HTTP_ORIGIN')!=origin:
                        start('403 Forbidden',[('Content-Length','0')]);return [b'']
                    if path.endswith('identity-aba'):identity_aba(fixture.path,'dev')
                    else:membership_change(fixture.store,fixture.path,'dev','revoked' if path.endswith('revoke-target') else 'active')
                    start('200 OK',[('Content-Length','0'),('Cache-Control','no-store')]);return [b'']
                return app(env,start)
            server.set_app(synthetic)
            serve_synthetic(server,on_ready=lambda:print(origin,flush=True))
    finally:fixture.doCleanups()

if __name__=='__main__':main()
