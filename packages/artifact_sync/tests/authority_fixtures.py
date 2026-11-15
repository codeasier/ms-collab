# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Explicit synthetic fixture seeding only; never imported by runtime packages.

These helpers construct reviewed synthetic grants/publications for old test cases.
They are not production migration or approval APIs and do not model their audit.
"""
from packages.artifact_sync import records
from packages.workspaces.authority import capture_authority


def seed_publication(store, scope, snapshot, *, actor='admin'):
    def operation(policy, db):
        record=records.load(db,scope)
        raw=records.snapshot_text(snapshot)
        bound=records.publication_text(raw,records.capture_publication(policy,actor,record,raw))
        db.execute(f'UPDATE {records._TABLE} SET published=?,published_revision=revision WHERE workspace=? AND connection=? AND bucket=?',
            (bound,*records.scope_key(scope)))
    store._transaction(operation,write=True,with_connection=True)


def configure_index_fixture(store, config, *, actor='approver', republish=True):
    def operation(policy,db):
        scope=config.connection.scope;old=records.load(db,scope)
        snapshot=old.published
        raw=records.bound_policy_text(config,capture_authority(policy,records.recipients(config)),old.grant_revision+1)
        db.execute(f'UPDATE {records._TABLE} SET policy=? WHERE workspace=? AND connection=? AND bucket=?',(raw,*records.scope_key(scope)))
        if snapshot is not None and republish:
            current=records.load(db,scope)
            bound=records.publication_text(snapshot,records.capture_publication(policy,actor,current,snapshot))
            db.execute(f'UPDATE {records._TABLE} SET published=? WHERE workspace=? AND connection=? AND bucket=?',(bound,*records.scope_key(scope)))
    store._transaction(operation,write=True,with_connection=True)


def configure_bytes_fixture(store, config):
    from packages.artifact_download import records as byte
    def operation(policy,db):
        old=byte.load(db,config.scope);index=records.load(db,config.scope)
        raw=byte.bound_policy_text(config,capture_authority(policy,config.downloaders),old.grant_revision+1,byte.parent_pin(index))
        db.execute(f'UPDATE {byte._TABLE} SET policy=? WHERE workspace=? AND connection=? AND bucket=?',(raw,*records.scope_key(config.scope)))
    store._transaction(operation,write=True,with_connection=True)
