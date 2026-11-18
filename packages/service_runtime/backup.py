# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Cold, coordinated backup and restore-to-new-directory only.

Backups are sensitive private data, not export artifacts. Session/pending login
rows are NEVER backed up. Restoration always creates a new empty login store.
"""
from contextlib import closing
from hashlib import sha256
import sqlite3
import os
from packages.login_state import SQLiteLoginStore
from packages.workspaces import SQLiteWorkspaceService, StorageError
from .config import RuntimeFailure, absolute, private, read_private, decode, load_config
from .runtime import (Lease, allocate_directory, canonical, write_new, sync_directory,
    connection, inspect, manifest, now_micros, MAX_DB, POLICY, LOGIN, LOCK, MANIFEST)
from .schema import layout_profile, schema_digest, _APPLICATION_ID, _SCHEMA_VERSION
from packages.workspace_admin_api.transfer_records import validate_all as validate_transfer_history

BACKUP_MANIFEST = 'backup.json'


def _disjoint(source, destination):
    # Never mutate a source installation/checkpoint by placing output inside it.
    # Callers already require absolute canonical paths without symlink ancestors.
    if source.is_relative_to(destination) or destination.is_relative_to(source):
        raise RuntimeFailure('overlapping_paths')
    # Existing ancestor identity also catches case-folding/bind aliases that
    # lexical paths cannot establish. This is not a hostile-filesystem race guard.
    for root, child in ((source, destination), (destination, source)):
        if root.exists():
            for ancestor in (child, *child.parents):
                if ancestor.exists() and os.path.samefile(root, ancestor):
                    raise RuntimeFailure('overlapping_paths')


def _verify(config, source):
    private(source, directory=True)
    if set(p.name for p in source.iterdir()) != {POLICY, BACKUP_MANIFEST}:
        raise RuntimeFailure('backup_invalid')
    meta = decode(read_private(source / BACKUP_MANIFEST, 4096))
    expected_keys = {'format','binding','policy_schema','login_schema','policy_sha256','policy_bytes','clock_floor','created_at','sessions_excluded'}
    if type(meta) is not dict or type(meta.get('format')) is not int:
        raise RuntimeFailure('backup_invalid')
    if meta['format'] == 1:
        profile = layout_profile(1)  # B1's registry entry is explicitly I1.
    elif meta['format'] == 2:
        expected_keys = expected_keys | {'installation_format'}
        if type(meta.get('installation_format')) is not int or meta['installation_format'] != 2:
            raise RuntimeFailure('backup_invalid')
        profile = layout_profile(2)  # B2 admits only the declared I2 pair.
    else:
        raise RuntimeFailure('backup_invalid')
    if (set(meta) != expected_keys
            or meta['binding'] != config.binding.fingerprint() or meta['policy_schema'] != profile.policy_digest
            or meta['login_schema'] != profile.login_digest or meta['sessions_excluded'] is not True
            or type(meta['policy_bytes']) is not int or not 0 < meta['policy_bytes'] <= MAX_DB
            or type(meta['clock_floor']) is not int or type(meta['created_at']) is not int
            or not 0 <= meta['clock_floor'] <= meta['created_at'] <= now_micros()):
        raise RuntimeFailure('backup_invalid')
    content = read_private(source / POLICY, MAX_DB)
    if len(content) != meta['policy_bytes'] or sha256(content).hexdigest() != meta['policy_sha256']:
        raise RuntimeFailure('backup_invalid')
    # Validate current exact schema and policy before allocating a restore target.
    # Digests detect accidental changes, not an adversary able to rewrite backup
    # and manifest together. Ownership and secure backup storage remain essential.
    with closing(sqlite3.connect(':memory:')) as db:
        db.execute('PRAGMA trusted_schema=OFF')
        db.deserialize(content)
        if (db.execute('PRAGMA application_id').fetchone()[0] != _APPLICATION_ID
                or db.execute('PRAGMA user_version').fetchone()[0] != _SCHEMA_VERSION
                or schema_digest(db) != profile.policy_digest
                or db.execute('PRAGMA quick_check').fetchall() != [('ok',)]
                or db.execute('PRAGMA foreign_key_check').fetchall()):
            raise RuntimeFailure('backup_invalid')
        model = object.__new__(SQLiteWorkspaceService)._load(db)
        if profile.format == 2:
            try:
                validate_transfer_history(db, model)
            except StorageError:
                raise RuntimeFailure('backup_invalid') from None
    return meta, content, profile


def verify_backup(config_path, source):
    config = load_config(config_path)
    try:
        _verify(config, absolute(str(source)))
        return {'backup_valid': True, 'sessions_excluded': True, 'live_restore_tested': False}
    except RuntimeFailure: raise
    except Exception: raise RuntimeFailure('backup_invalid') from None


def backup(config_path, destination):
    config = load_config(config_path); destination = absolute(str(destination))
    _disjoint(config.directory, destination)
    try:
        # No service using this runtime may remain open. SQLite additionally
        # reserves both attached DBs before capturing policy and security time.
        with Lease(config.directory, exclusive=True), closing(connection(config.directory, writable=True)) as db:
            inspected = inspect(config, db)
            profile, floor = inspected.profile, inspected.clock_floor
            content = db.serialize(name='main')
            if not 0 < len(content) <= MAX_DB:
                raise RuntimeFailure('invalid_storage')
            meta = {'format':1 if profile.format == 1 else 2, 'binding':config.binding.fingerprint(),
                'policy_schema':profile.policy_digest, 'login_schema':profile.login_digest,
                'policy_sha256':sha256(content).hexdigest(),'policy_bytes':len(content),
                'clock_floor':floor,'created_at':max(floor,now_micros()),'sessions_excluded':True}
            if profile.format == 2:
                meta['installation_format'] = 2
            allocate_directory(destination)
            write_new(destination / POLICY, content)
            write_new(destination / BACKUP_MANIFEST, canonical(meta))
            sync_directory(destination); sync_directory(destination.parent)
        _verify(config, destination)
        return {'backup_valid':True,'sessions_excluded':True,'live_restore_tested':False}
    except RuntimeFailure: raise
    except Exception: raise RuntimeFailure('backup_incomplete') from None


def restore(config_path, source):
    config = load_config(config_path); source = absolute(str(source))
    _disjoint(source, config.directory)
    try:
        meta, content, profile = _verify(config, source)
        allocate_directory(config.directory)
        write_new(config.directory / LOCK, b'')
        with Lease(config.directory, exclusive=True):
            write_new(config.directory / POLICY, content)
            SQLiteLoginStore.initialize(config.directory / LOGIN, binding=config.binding)
            # A fresh store drops every pre-backup session, challenge and verifier;
            # never resurrect a revoked login by restoring old authentication rows.
            with sqlite3.connect(config.directory / LOGIN) as db:
                db.execute('UPDATE metadata SET clock=? WHERE id=1', (max(meta['clock_floor'],now_micros()),))
            write_new(config.directory / MANIFEST, canonical(manifest(config, layout=profile.format)))
            sync_directory(config.directory); sync_directory(config.directory.parent)
            with closing(connection(config.directory)) as db: inspect(config, db)
        return {'restore_valid':True,'sessions_restored':False,'login_enabled':False,'production_ready':False}
    except RuntimeFailure: raise
    except Exception: raise RuntimeFailure('restore_incomplete') from None
