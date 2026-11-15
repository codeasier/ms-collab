"""Synthetic staged/published metadata, never imported by production factories."""
from dataclasses import astuple, replace
from pathlib import Path
import sqlite3

from packages.application_host import ApplicationHost
from packages.application_host.tests.fixtures import setup, NOW
from packages.application_ui import ApplicationUI
from packages.artifacts import Artifact, Scope, Snapshot, Definition, SavedView
from packages.artifacts_api import view_resource
from packages.artifact_sync.records import snapshot_text
from packages.artifact_sync.tests.authority_fixtures import seed_publication

SCOPE = Scope('alpha','source','bucket')
PUBLISHED = Snapshot(SCOPE,'2026-01-01T00:00:00Z',(
    Artifact('builds/','2026-01-01T00:00:00Z',0,True),
    Artifact('builds/<svg onload=alert(1)>.txt','2026-01-01T00:00:00Z',12),))
CANDIDATE = replace(PUBLISHED, artifacts=(*PUBLISHED.artifacts,
    Artifact('builds/private-candidate.txt','2026-01-01T00:00:00Z',20)))


def application(directory, *, origin='https://app.example.invalid', clock=lambda:NOW, authorization_endpoint=None, administration=True, enabled=True):
    original,policy,login,exchange=setup(directory,origin=origin,clock=clock,authorization_endpoint=authorization_endpoint,enabled=enabled)
    args=dict(exchange=exchange,authorization_endpoint=authorization_endpoint or 'https://identity.example.invalid/authorize',authorization_scopes=('synthetic_user',)) if enabled else {}
    host=ApplicationHost(config=replace(original._config,artifact_administration=administration),sessions=original._boundary._sessions,clock=clock,**args)
    view=view_resource(SavedView(SCOPE,'files','Synthetic files',Definition(files_only=True,recursive=True)))
    seed_publication(policy,SCOPE,PUBLISHED)
    with sqlite3.connect(Path(directory)/'policy.sqlite') as db:
        db.execute('UPDATE artifact_indexes_v1 SET candidate=?,revision=2 WHERE workspace=?',(snapshot_text(CANDIDATE),'alpha'))
        db.execute('INSERT INTO resources VALUES (?,?,?,?,?)',astuple(view))
    return ApplicationUI(host=host),policy,login,exchange


def stage(directory):
    with sqlite3.connect(Path(directory)/'policy.sqlite') as db:
        db.execute('UPDATE artifact_indexes_v1 SET candidate=?,revision=revision+1 WHERE workspace=?',(snapshot_text(CANDIDATE),'alpha'))
        return db.execute('SELECT revision FROM artifact_indexes_v1 WHERE workspace=?',('alpha',)).fetchone()[0]
