"""Synthetic cookie authority and bounded listing, never production credentials."""
from datetime import timedelta
from packages.application_host import ApplicationHost, HostConfig
from packages.application_host.tests import fixtures as host
from packages.artifact_refresh import ArtifactRefreshConfiguration, initialize_refresh
from packages.artifact_sync.tests.fixtures import credential, page


class Pages:
    def __init__(self):
        self.loads=[]; self.calls=[]; self.load_hook=lambda:None; self.page_hook=lambda:None
        self.rows=(('builds/new.txt','2026-01-01T23:00:00Z',12),)
    def load(self,ref,scope,origin):
        self.loads.append((ref,scope,origin));self.load_hook()
        return credential(scope,origin)
    def list_page(self,connection,secret,marker,now,*,remaining):
        self.calls.append((connection.scope,marker,remaining));self.page_hook()
        return page(self.rows,config=connection)


def setup(directory,*,origin=host.ORIGIN,clock=lambda:host.NOW,enabled=True,schema=True,authorization_endpoint=None):
    base,policy,login,exchange=host.setup(directory,origin=origin,clock=clock,modules=('artifacts',),authorization_endpoint=authorization_endpoint)
    if schema:initialize_refresh(policy)
    pages=Pages()
    app=ApplicationHost(config=HostConfig(origin,('artifacts',),artifact_administration=True,artifact_refresh=enabled),
        sessions=base._boundary._sessions,clock=clock,exchange=exchange,
        authorization_endpoint=authorization_endpoint or host.ISSUER+'/authorize',authorization_scopes=('synthetic_user',),
        artifact_refresh=ArtifactRefreshConfiguration(pages.load,pages) if enabled else None)
    return app,policy,login,exchange,pages
