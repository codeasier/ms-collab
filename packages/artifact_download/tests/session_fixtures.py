"""Synthetic bytes and bound credentials. Never imported by production factories."""
from dataclasses import replace
from hashlib import sha256
import io
from pathlib import Path
import sqlite3

from packages.application_host import ApplicationHost
from packages.application_host.tests.fixtures import setup, NOW
from packages.artifact_download import ArtifactDownloadConfiguration, ContentBinding, DownloadPolicy, initialize_downloads
from packages.artifact_sync.provider import BoundCredential
from packages.artifact_sync.records import snapshot_text
from packages.artifact_sync.tests.authority_fixtures import seed_publication
from packages.artifacts import Artifact, Scope, Snapshot

SCOPE=Scope('alpha','source','bucket')
KEY='builds/<svg onload=alert(1)>.bin'
DATA=b'synthetic-download-not-production\x00'*5000
BINDING=ContentBinding(KEY,len(DATA),'2026-01-01T00:00:00Z','"synthetic-v1"',sha256(DATA).hexdigest())
GRANT=DownloadPolicy(SCOPE,('admin',),(BINDING,))


class Response:
    def __init__(self,data,hook):self._data=io.BytesIO(data);self.closed=False;self.hook=hook
    def read(self,n):result=self._data.read(n);self.hook();return result
    def close(self):self.closed=True;self._data.close()


class SyntheticObjects:
    def __init__(self):self.calls=[];self.loads=[];self.responses=[];self.data=DATA;self.hook=lambda:None;self.read_hook=lambda:None
    def loader(self,reference,scope,origin):
        self.loads.append((reference,scope,origin))
        return BoundCredential(scope,origin,'SyntheticAccess01','synthetic-secret-not-an-account')
    def open(self,config,secret,binding,now,*,remaining):
        self.calls.append((config.scope,binding));self.hook();r=Response(self.data,self.read_hook);self.responses.append(r);return r


def setup_download(directory,*,origin='https://app.example.invalid',clock=lambda:NOW,authorization_endpoint=None,enabled=True):
    original,policy,login,exchange=setup(directory,origin=origin,clock=clock,authorization_endpoint=authorization_endpoint)
    initialize_downloads(policy,(GRANT,))
    snapshot=Snapshot(SCOPE,'2026-01-01T00:00:00Z',(Artifact('builds/','2026-01-01T00:00:00Z',0,True),Artifact(KEY,BINDING.modified_at,len(DATA)),Artifact('builds/unbound.txt',BINDING.modified_at,7)))
    seed_publication(policy,SCOPE,snapshot)
    objects=SyntheticObjects()
    config=replace(original._config,artifact_downloads=enabled)
    args=dict(exchange=exchange,authorization_endpoint=authorization_endpoint or 'https://identity.example.invalid/authorize',authorization_scopes=('synthetic_user',))
    host=ApplicationHost(config=config,sessions=original._boundary._sessions,clock=clock,
        artifact_downloads=ArtifactDownloadConfiguration(objects.loader,objects) if enabled else None,**args)
    return host,policy,login,exchange,objects
