"""Fictional installation/configuration/state only; no real identities or hosts."""
from contextlib import redirect_stdout, closing
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from packages.service_runtime import initialize, check, create_application, backup, restore, verify_backup, load_config, RuntimeFailure
from packages.service_runtime.__main__ import main
from packages.service_runtime.runtime import connection, Lease, manifest, canonical, now_micros
from packages.service_runtime.schema import POLICY_DIGEST, LOGIN_DIGEST, schema_digest
from packages.application_ui.tests.test_application import request


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.config = self.root/'config.json'; self.directory=self.root/'state'
        self.value = {'version':1,'origin':'https://app.example.invalid','data_directory':str(self.directory),
            'modules':['community','responsibility','pipelines','artifacts','research-assets','assistant','gui-smoke'],
            'artifact_administration':True,
            'login':{'mode':'disabled','issuer':'https://identity.example.invalid','audience':'synthetic-client'}}
        self.save()
    def save(self):
        self.config.write_text(json.dumps(self.value)); self.config.chmod(0o600)
    def init(self): initialize(self.config)
    def changed(self, path, value):
        root=self.value
        for key in path[:-1]:root=root[key]
        root[path[-1]]=value; self.save()
    def app(self):
        app=create_application(self.config);self.addCleanup(app.close);return app
    def hashes(self):
        return {n:sha256((self.directory/n).read_bytes()).hexdigest() for n in ('policy.sqlite','login.sqlite','installation.json')}
    def restored_config(self):
        self.value['data_directory']=str(self.root/'restored');self.save()

    def test_initialization_creates_private_empty_state_not_accounts_or_grants(self):
        self.init()
        self.assertEqual(self.directory.stat().st_mode & 0o777,0o700)
        for item in self.directory.iterdir(): self.assertEqual(item.stat().st_mode & 0o777,0o600)
        with sqlite3.connect(self.directory/'policy.sqlite') as db:
            for table in ('principals','operators','workspaces','memberships','modules','resources','community_connections','artifact_indexes_v1','assistant_conversations','research_catalog','gui_smoke_snapshots','gui_smoke_grants'):
                self.assertEqual(db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0],0)
            self.assertEqual(schema_digest(db),POLICY_DIGEST)
        with sqlite3.connect(self.directory/'login.sqlite') as db:
            self.assertEqual(schema_digest(db),LOGIN_DIGEST)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0],0)
        self.assertFalse(check(self.config)['production_ready'])
    def test_authority_v3_policy_v2_login_and_closed_insert_survive_restore(self):
        self.init()
        with sqlite3.connect(self.directory/'policy.sqlite') as db:
            realm = db.execute('SELECT realm FROM metadata').fetchone()[0]
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 3)
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("INSERT INTO principals VALUES('blocked','https://identity.example.invalid','blocked',1,1,1)")
        with sqlite3.connect(self.directory/'login.sqlite') as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 2)
        backup(self.config,self.root/'backup');self.restored_config();restore(self.config,self.root/'backup')
        with sqlite3.connect(self.root/'restored'/'policy.sqlite') as db:
            self.assertEqual(db.execute('SELECT realm FROM metadata').fetchone()[0],realm)
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("INSERT INTO principals VALUES('blocked','https://identity.example.invalid','blocked',1,1,1)")
        self.assertFalse(check(self.config)['login_enabled'])

    def test_v2_policy_layout_and_backup_are_rejected_without_auto_grant_upgrade(self):
        self.init();source=self.root/'legacy-v2-backup';backup(self.config,source)
        def downgrade(path):
            with sqlite3.connect(path) as db:
                db.execute('DROP TABLE membership_authority')
                db.execute('DROP TABLE operator_authority')
                db.execute('PRAGMA user_version=2')
                return schema_digest(db)
        old_digest=downgrade(source/'policy.sqlite')
        payload=(source/'policy.sqlite').read_bytes()
        meta=json.loads((source/'backup.json').read_text())
        meta.update(policy_schema=old_digest,policy_bytes=len(payload),policy_sha256=sha256(payload).hexdigest())
        (source/'backup.json').write_bytes(canonical(meta))
        before={p.name:p.read_bytes() for p in source.iterdir()}
        with self.assertRaises(RuntimeFailure):verify_backup(self.config,source)
        downgrade(self.directory/'policy.sqlite')
        installed=json.loads((self.directory/'installation.json').read_text());installed['policy_schema']=old_digest
        (self.directory/'installation.json').write_bytes(canonical(installed));prior=self.hashes()
        with self.assertRaises(RuntimeFailure):check(self.config)
        self.assertEqual(prior,self.hashes());self.restored_config()
        with self.assertRaises(RuntimeFailure):restore(self.config,source)
        self.assertFalse((self.root/'restored').exists())
        self.assertEqual(before,{p.name:p.read_bytes() for p in source.iterdir()})

    def test_old_gui_schema_installation_and_backup_are_rejected_without_rewrite(self):
        self.init();source=self.root/'old-backup';backup(self.config,source)
        # Synthetic prior-layout checkpoint with a self-consistent old manifest.
        with sqlite3.connect(source/'policy.sqlite') as db:
            db.execute('DROP TABLE gui_smoke_grants');old_digest=schema_digest(db)
        payload=(source/'policy.sqlite').read_bytes()
        meta=json.loads((source/'backup.json').read_text())
        meta.update(policy_schema=old_digest,policy_bytes=len(payload),policy_sha256=sha256(payload).hexdigest())
        (source/'backup.json').write_bytes(canonical(meta))
        before={p.name:p.read_bytes() for p in source.iterdir()}
        with self.assertRaises(RuntimeFailure):verify_backup(self.config,source)
        with sqlite3.connect(self.directory/'policy.sqlite') as db:db.execute('DROP TABLE gui_smoke_grants')
        installation=json.loads((self.directory/'installation.json').read_text());installation['policy_schema']=old_digest
        (self.directory/'installation.json').write_bytes(canonical(installation));prior=self.hashes()
        with self.assertRaises(RuntimeFailure):check(self.config)
        self.assertEqual(prior,self.hashes())
        self.restored_config()
        with self.assertRaises(RuntimeFailure):restore(self.config,source)
        self.assertFalse((self.root/'restored').exists())
        self.assertEqual(before,{p.name:p.read_bytes() for p in source.iterdir()})

    def test_legacy_admin_retry_installation_and_backup_do_not_auto_migrate(self):
        from packages.workspace_admin_api.requests import _TABLE, _LEGACY_CREATE
        self.init();source=self.root/'legacy-admin-backup';backup(self.config,source)
        with sqlite3.connect(source/'policy.sqlite') as db:
            db.execute('DROP TABLE '+_TABLE);db.execute(_LEGACY_CREATE);old_digest=schema_digest(db)
        payload=(source/'policy.sqlite').read_bytes()
        meta=json.loads((source/'backup.json').read_text())
        meta.update(policy_schema=old_digest,policy_bytes=len(payload),policy_sha256=sha256(payload).hexdigest())
        (source/'backup.json').write_bytes(canonical(meta))
        before={p.name:p.read_bytes() for p in source.iterdir()}
        with self.assertRaises(RuntimeFailure):verify_backup(self.config,source)
        with sqlite3.connect(self.directory/'policy.sqlite') as db:
            db.execute('DROP TABLE '+_TABLE);db.execute(_LEGACY_CREATE)
        installed=json.loads((self.directory/'installation.json').read_text());installed['policy_schema']=old_digest
        (self.directory/'installation.json').write_bytes(canonical(installed));prior=self.hashes()
        with self.assertRaises(RuntimeFailure):check(self.config)
        self.assertEqual(prior,self.hashes());self.restored_config()
        with self.assertRaises(RuntimeFailure):restore(self.config,source)
        self.assertFalse((self.root/'restored').exists())
        self.assertEqual(before,{p.name:p.read_bytes() for p in source.iterdir()})

    def test_check_is_read_only_and_no_network_no_auto_creation(self):
        with self.assertRaises(RuntimeFailure): check(self.config)
        self.assertFalse(self.directory.exists()); self.init(); before=self.hashes()
        with patch('socket.socket',side_effect=AssertionError('no network')):
            self.assertTrue(check(self.config)['installation_valid'])
        self.assertEqual(before,self.hashes())
    def test_factory_never_creates_missing_storage(self):
        with self.assertRaises(RuntimeFailure): self.app()
        self.assertFalse(self.directory.exists())
    def test_reinitialization_never_replaces_existing_state(self):
        self.init(); before=self.hashes()
        with self.assertRaisesRegex(RuntimeFailure,'destination_exists'):self.init()
        self.assertEqual(before,self.hashes())
    def test_public_ui_and_all_identity_and_data_routes_fail_closed(self):
        self.init();app=self.app()
        self.assertEqual(request(app,'/')['status'],200)
        result=request(app,'/app/bootstrap')['body']['data'];self.assertFalse(result['login_configured']);self.assertIsNone(result['authorization_endpoint'])
        for endpoint in ('/auth/session','/api/workspaces','/api/workspaces/alpha/pipelines/source/build/runs'):
            self.assertEqual(request(app,endpoint)['status'],401)
        self.assertEqual(request(app,'/auth/start',method='POST',origin=self.value['origin'],body={},extra={'HTTP_X_MS_COLLAB_LOGIN':'1'})['status'],503)
        with sqlite3.connect(self.directory/'login.sqlite') as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0],0)
    def test_no_bearer_header_proxy_or_foreign_origin_bridge(self):
        self.init();app=self.app()
        for extra in ({'HTTP_AUTHORIZATION':'Bearer marker'},{'HTTP_HOST':'attacker.invalid'},{'HTTP_ORIGIN':'https://other.invalid'},{'wsgi.url_scheme':'http'}):
            self.assertEqual(request(app,'/app/bootstrap',extra=extra)['status'],401)
    def test_closed_application_does_not_serve_cached_private_state(self):
        self.init(); app=self.app();app.close();self.assertEqual(request(app,'/')['status'],503)
    def test_missing_and_malformed_manifest_reject_startup(self):
        self.init();(self.directory/'installation.json').unlink()
        with self.assertRaises(RuntimeFailure):self.app()
    def test_corrupt_schema_unknown_trigger_and_wrong_journal_reject(self):
        self.init()
        with sqlite3.connect(self.directory/'policy.sqlite') as db:db.execute('CREATE TRIGGER unexpected AFTER INSERT ON principals BEGIN SELECT 1; END')
        with self.assertRaises(RuntimeFailure):check(self.config)
        with sqlite3.connect(self.directory/'policy.sqlite') as db:db.execute('DROP TRIGGER unexpected');db.execute('PRAGMA journal_mode=WAL')
        with self.assertRaises(RuntimeFailure):check(self.config)
    def test_login_clock_rollback_and_binding_change_reject(self):
        self.init()
        with sqlite3.connect(self.directory/'login.sqlite') as db:db.execute('UPDATE metadata SET clock=9223372036854775807')
        with self.assertRaises(RuntimeFailure):check(self.config)
        self.changed(['login','audience'],'other-client')
        with self.assertRaises(RuntimeFailure):self.app()
    def test_snapshot_schema_and_foreign_reference_corruption_reject(self):
        self.init()
        with sqlite3.connect(self.directory/'policy.sqlite') as db:db.execute("INSERT INTO operators VALUES('missing')")
        with self.assertRaises(RuntimeFailure):check(self.config)
    def test_permission_symlink_hardlink_and_alias_fail_closed(self):
        self.init();file=self.directory/'login.sqlite';file.chmod(0o644)
        with self.assertRaises(RuntimeFailure):check(self.config)
        file.chmod(0o600);os.link(file,self.root/'alias')
        with self.assertRaises(RuntimeFailure):check(self.config)
        (self.root/'alias').unlink();file.rename(self.root/'login-moved');file.symlink_to(self.root/'login-moved')
        with self.assertRaises(RuntimeFailure):check(self.config)
    def test_config_duplicate_unknown_oversize_and_null_are_rejected(self):
        for raw in ('null','{"version":1,"version":1}','{"secret":"PRIVATE_MARKER"}', ' '*16385):
            self.config.write_text(raw)
            with self.assertRaises(RuntimeFailure):load_config(self.config)
    def test_configuration_does_not_accept_provider_or_dynamic_code_switches(self):
        for value in ('gitcode','enabled',True,None):
            self.changed(['login','mode'],value)
            with self.assertRaisesRegex(RuntimeFailure,'provider_not_enabled'):load_config(self.config)
        self.changed(['login','mode'],'disabled');self.value['login']['exchange']='malicious.module:Factory';self.save()
        with self.assertRaises(RuntimeFailure):load_config(self.config)
    def test_unknown_modules_dependency_boolean_and_origin_rejected(self):
        valid=json.loads(json.dumps(self.value))
        for key,value in (('modules',['raw-resources']),('modules',['responsibility']),('modules',['assistant','assistant']),('modules','assistant'),('artifact_administration',1),('origin','http://app.example.invalid'),('origin','https://app.example.invalid/path'),('version',True)):
            self.value=json.loads(json.dumps(valid));self.value[key]=value;self.save()
            with self.assertRaises(RuntimeFailure):load_config(self.config)
    def test_config_must_be_owned_private_and_absolute(self):
        self.config.chmod(0o644)
        with self.assertRaises(RuntimeFailure):load_config(self.config)
        self.config.chmod(0o600);self.value['data_directory']='relative';self.save()
        with self.assertRaises(RuntimeFailure):load_config(self.config)
        self.value['data_directory']=str(self.root/'sub'/'..'/'state');self.save()
        with self.assertRaises(RuntimeFailure):load_config(self.config)
    def test_partial_initialization_is_not_reused_or_silently_removed(self):
        with patch('packages.service_runtime.runtime.SQLiteLoginStore.initialize',side_effect=RuntimeError('PRIVATE_MARKER')):
            with self.assertRaisesRegex(RuntimeFailure,'initialization_incomplete'):self.init()
        self.assertTrue((self.directory/'policy.sqlite').exists())
        self.assertFalse((self.directory/'installation.json').exists())
        with self.assertRaises(RuntimeFailure):self.app()
        with self.assertRaisesRegex(RuntimeFailure,'destination_exists'):self.init()
    def test_backup_requires_exclusive_lease_and_never_replaces(self):
        self.init();app=self.app();destination=self.root/'backup'
        with self.assertRaisesRegex(RuntimeFailure,'runtime_busy'):backup(self.config,destination)
        self.assertFalse(destination.exists());app.close();backup(self.config,destination)
        old=(destination/'policy.sqlite').read_bytes()
        with self.assertRaisesRegex(RuntimeFailure,'destination_exists'):backup(self.config,destination)
        self.assertEqual(old,(destination/'policy.sqlite').read_bytes())
    def test_maintenance_blocks_startup_and_check(self):
        self.init()
        with Lease(self.directory,exclusive=True):
            with self.assertRaises(RuntimeFailure):self.app()
            with self.assertRaises(RuntimeFailure):check(self.config)
    def test_independent_sqlite_writer_blocks_backup_before_allocating_output(self):
        self.init()
        for name in ('policy.sqlite','login.sqlite'):
            with closing(sqlite3.connect(self.directory/name,isolation_level=None)) as writer:
                writer.execute('BEGIN IMMEDIATE')
                with self.assertRaises(RuntimeFailure):backup(self.config,self.root/'backup')
                self.assertFalse((self.root/'backup').exists())
                writer.rollback()
    def test_backup_and_restore_interruption_never_yield_a_valid_partial_candidate(self):
        self.init()
        from packages.service_runtime import backup as backup_action
        from packages.service_runtime.runtime import write_new as real_write
        def fail_manifest(path, content):
            if path.name=='backup.json':raise OSError('PRIVATE_MARKER')
            return real_write(path, content)
        with patch('packages.service_runtime.backup.write_new',side_effect=fail_manifest):
            with self.assertRaises(RuntimeFailure):backup_action(self.config,self.root/'broken')
        with self.assertRaises(RuntimeFailure):verify_backup(self.config,self.root/'broken')
        self.assertTrue((self.root/'broken'/'policy.sqlite').exists())
        backup_action(self.config,self.root/'backup');self.restored_config()
        with patch('packages.service_runtime.backup.SQLiteLoginStore.initialize',side_effect=OSError('PRIVATE_MARKER')):
            with self.assertRaises(RuntimeFailure):restore(self.config,self.root/'backup')
        self.assertFalse((self.root/'restored'/'installation.json').exists())
        with self.assertRaises(RuntimeFailure):self.app()
    def test_backup_does_not_modify_source_and_excludes_pending_secret(self):
        self.init()
        with sqlite3.connect(self.directory/'login.sqlite') as db:
            db.execute("INSERT INTO attempts VALUES('state','browser','PRIVATE_VERIFIER_MARKER',1,2,NULL,NULL)")
            db.execute("INSERT INTO sessions VALUES('session','csrf','synthetic-subject',1,2,NULL,NULL,NULL)")
        before=self.hashes();backup(self.config,self.root/'backup');self.assertEqual(before,self.hashes())
        self.assertEqual({p.name for p in (self.root/'backup').iterdir()},{'policy.sqlite','backup.json'})
        self.assertNotIn(b'PRIVATE_VERIFIER_MARKER',(self.root/'backup'/'policy.sqlite').read_bytes())
        self.restored_config();restore(self.config,self.root/'backup')
        with sqlite3.connect(self.root/'restored'/'login.sqlite') as db:
            for name in ('sessions','attempts'):self.assertEqual(db.execute('SELECT COUNT(*) FROM '+name).fetchone()[0],0)
    def test_round_trip_validates_new_schema_and_preserves_business_rows(self):
        self.init()
        with sqlite3.connect(self.directory/'policy.sqlite') as db:
            # TEST-ONLY synthetic legacy business data. Production provisioning
            # is closed; never turn this schema bypass into a runtime entry.
            from packages.workspaces.sqlite import _PRINCIPAL_GUARDS
            db.execute('DROP TRIGGER principal_replace_closed')
            db.execute("INSERT INTO principals VALUES('member','https://identity.example.invalid','fictional-subject',0,1,1)")
            db.execute("INSERT INTO principals VALUES('admin','https://identity.example.invalid','fictional-admin',1,1,1)")
            db.execute(_PRINCIPAL_GUARDS[2])
            db.execute("INSERT INTO workspaces VALUES('alpha','Synthetic team','product','suspended',2)")
            db.execute("INSERT INTO memberships VALUES('alpha','admin','admin','active',1)")
            db.execute("INSERT INTO membership_authority VALUES('alpha','admin',NULL,NULL,NULL,NULL)")
        backup(self.config,self.root/'backup');self.assertTrue(verify_backup(self.config,self.root/'backup')['backup_valid'])
        self.restored_config();self.assertFalse(restore(self.config,self.root/'backup')['sessions_restored'])
        self.assertTrue(check(self.config)['installation_valid'])
        with sqlite3.connect(self.root/'restored'/'policy.sqlite') as db:
            self.assertEqual(db.execute("SELECT active FROM principals WHERE id='member'").fetchall(),[(0,)])
            self.assertEqual(db.execute('SELECT state,revision FROM workspaces').fetchall(),[('suspended',2)])
    def test_nested_backup_output_does_not_modify_source_installation(self):
        self.init(); before=self.hashes();names={p.name for p in self.directory.iterdir()}
        for destination in (self.directory, self.directory/'checkpoint', self.root):
            with self.assertRaisesRegex(RuntimeFailure,'overlapping_paths'):backup(self.config,destination)
        self.assertFalse((self.directory/'checkpoint').exists())
        self.assertEqual(before,self.hashes());self.assertEqual(names,{p.name for p in self.directory.iterdir()})
    def test_overlapping_restore_target_does_not_damage_source_checkpoint(self):
        self.init();source=self.root/'backup';backup(self.config,source)
        before={p.name:p.read_bytes() for p in source.iterdir()}
        for target in (source,source/'restored',self.root):
            self.value['data_directory']=str(target);self.save()
            with self.assertRaisesRegex(RuntimeFailure,'overlapping_paths'):restore(self.config,source)
            self.assertTrue(verify_backup(self.config,source)['backup_valid'])
            self.assertEqual(before,{p.name:p.read_bytes() for p in source.iterdir()})
        self.assertFalse((source/'restored').exists())
    def test_double_slash_aliases_reject_before_allocation_and_preserve_source(self):
        self.init();source=self.root/'backup';backup(self.config,source)
        before={p.name:p.read_bytes() for p in source.iterdir()}
        with self.assertRaises(RuntimeFailure):load_config('/'+str(self.config))
        with self.assertRaises(RuntimeFailure):backup(self.config,'/'+str(self.directory/'nested'))
        with self.assertRaises(RuntimeFailure):verify_backup(self.config,'/'+str(source))
        self.restored_config()
        with self.assertRaises(RuntimeFailure):restore(self.config,'/'+str(source))
        self.value['data_directory']='/'+str(source/'restored');self.save()
        with self.assertRaises(RuntimeFailure):restore(self.config,source)
        self.assertFalse((source/'restored').exists());self.assertFalse((self.directory/'nested').exists())
        self.assertFalse((self.root/'restored').exists())
        self.assertEqual(before,{p.name:p.read_bytes() for p in source.iterdir()})
    def test_existing_filesystem_alias_is_not_decided_by_lexical_path_alone(self):
        self.init();source=self.root/'backup';backup(self.config,source)
        before={p.name:p.read_bytes() for p in source.iterdir()}
        self.restored_config()
        # Linux cannot create a case-folding alias in this fixture; model only
        # the physical identity comparison, without weakening lexical checks.
        with patch('packages.service_runtime.backup.os.path.samefile',return_value=True):
            with self.assertRaisesRegex(RuntimeFailure,'overlapping_paths'):restore(self.config,source)
        self.assertFalse((self.root/'restored').exists())
        self.assertEqual(before,{p.name:p.read_bytes() for p in source.iterdir()})
    def test_restore_refuses_live_existing_destination_and_corrupt_backup(self):
        self.init();backup(self.config,self.root/'backup')
        with self.assertRaisesRegex(RuntimeFailure,'destination_exists'):restore(self.config,self.root/'backup')
        self.restored_config();file=self.root/'backup'/'policy.sqlite';file.write_bytes(file.read_bytes()+b'tampered')
        with self.assertRaises(RuntimeFailure):restore(self.config,self.root/'backup')
        self.assertFalse((self.root/'restored').exists())
    def test_restore_rejects_extra_archive_file_wrong_binding_or_future_clock(self):
        self.init();backup(self.config,self.root/'backup');self.restored_config();extra=self.root/'backup'/'unexpected';extra.write_text('x')
        with self.assertRaises(RuntimeFailure):verify_backup(self.config,self.root/'backup')
        extra.unlink();self.changed(['login','audience'],'different-client')
        with self.assertRaises(RuntimeFailure):restore(self.config,self.root/'backup')
        self.changed(['login','audience'],'synthetic-client');meta=self.root/'backup'/'backup.json';data=json.loads(meta.read_text());data['created_at']=9223372036854775807;meta.write_text(json.dumps(data))
        with self.assertRaises(RuntimeFailure):restore(self.config,self.root/'backup')
    def test_no_implicit_upgrade_and_no_downgrade_override(self):
        self.init()
        with sqlite3.connect(self.directory/'policy.sqlite') as db:db.execute('PRAGMA user_version=99')
        before=self.hashes()
        with self.assertRaises(RuntimeFailure):check(self.config)
        self.assertEqual(before,self.hashes())
    def test_cli_output_sanitizes_errors_and_never_claims_production(self):
        self.init();stream=io.StringIO()
        with redirect_stdout(stream):self.assertEqual(main(['--config',str(self.config),'check','--require-production']),3)
        self.assertEqual(json.loads(stream.getvalue())['error'],'production_acceptance_required')
        stream=io.StringIO()
        with redirect_stdout(stream):self.assertEqual(main(['--config',str(self.config),'PRIVATE_MARKER']),2)
        self.assertNotIn('PRIVATE_MARKER',stream.getvalue());self.assertNotIn(str(self.root),stream.getvalue())
    def test_deleted_or_replaced_lease_does_not_keep_application_serving(self):
        self.init();app=self.app();lock=self.directory/'.runtime.lock';lock.unlink();lock.write_bytes(b'');lock.chmod(0o600)
        self.assertEqual(request(app,'/')['status'],503)

    def _installed_manifest_source(self):
        # Compare every source entry, including the lease file, not just DB hashes.
        return (self.config.read_bytes(),
                {p.name: p.read_bytes() for p in self.directory.iterdir()})

    def _installed_manifest_login(self):
        # Observe real committed state without running a store constructor or
        # opening a writable connection. SELECT * retains every stored column.
        uri = (self.directory/'login.sqlite').as_uri() + '?mode=ro'
        with closing(sqlite3.connect(uri, uri=True, isolation_level=None)) as db:
            db.execute('BEGIN')
            metadata = db.execute('SELECT * FROM metadata ORDER BY id').fetchall()
            self.assertEqual(len(metadata), 1)
            self.assertEqual(len(metadata[0]), 3)
            identifier, binding, clock = metadata[0]
            self.assertIs(type(clock), int)
            state = {
                'schema': db.execute('SELECT * FROM sqlite_schema ORDER BY type,name,tbl_name').fetchall(),
                'application_id': db.execute('PRAGMA application_id').fetchone(),
                'user_version': db.execute('PRAGMA user_version').fetchone(),
                'journal_mode': db.execute('PRAGMA journal_mode').fetchone(),
                'metadata': (identifier, binding),
                'attempts': db.execute('SELECT * FROM attempts ORDER BY state').fetchall(),
                'sessions': db.execute('SELECT * FROM sessions ORDER BY token').fetchall(),
            }
        return state, clock

    def _installed_manifest_application(self):
        app = create_application(self.config)
        try:
            self.assertTrue(app.lease.valid())
        finally:
            app.close()

    def _assert_installed_manifest_entrypoints(self, raw, *, case, error=None):
        (self.directory/'installation.json').write_bytes(raw)
        destination = self.root/('manifest-backup-' + case)
        operations = (
            ('check', lambda: check(self.config)),
            ('factory', self._installed_manifest_application),
            ('backup', lambda: backup(self.config, destination)),
        )
        for name, operation in operations:
            with self.subTest(case=case, entrypoint=name):
                before = self._installed_manifest_source()
                root_entries = {p.name for p in self.root.iterdir()}
                self.assertFalse(destination.exists())
                successful_factory = error is None and name == 'factory'
                if successful_factory:
                    login_before, clock_before = self._installed_manifest_login()
                    self.assertEqual(before, self._installed_manifest_source())
                if error is None:
                    result = operation()
                    if successful_factory:
                        finished = now_micros()
                    if name != 'factory':
                        self.assertTrue(result['installation_valid' if name == 'check' else 'backup_valid'])
                else:
                    # Patch the function as resolved by backup, not its runtime alias.
                    with patch('packages.service_runtime.backup.allocate_directory') as allocator:
                        with self.assertRaises(RuntimeFailure) as raised:
                            operation()
                        code = 'runtime_unavailable' if name == 'factory' else error
                        self.assertEqual(raised.exception.code, code)
                        self.assertEqual(str(raised.exception), code)
                        allocator.assert_not_called()
                if successful_factory:
                    # Existing SQLiteLoginStore construction maintains its safety
                    # clock. No other logical login state may change on success.
                    login_after, clock_after = self._installed_manifest_login()
                    self.assertEqual(login_before, login_after)
                    self.assertLessEqual(clock_before, clock_after)
                    self.assertLessEqual(clock_after, finished)
                    after = self._installed_manifest_source()
                    self.assertEqual(before[0], after[0])
                    self.assertEqual(set(before[1]), set(after[1]))
                    self.assertEqual({k: v for k, v in before[1].items() if k != 'login.sqlite'},
                                     {k: v for k, v in after[1].items() if k != 'login.sqlite'})
                    lease_baseline = after
                else:
                    self.assertEqual(before, self._installed_manifest_source())
                    lease_baseline = before
                if error is None and name == 'backup':
                    self.assertEqual({p.name for p in destination.iterdir()}, {'policy.sqlite', 'backup.json'})
                    root_entries.add(destination.name)
                else:
                    self.assertFalse(destination.exists())
                self.assertEqual(root_entries, {p.name for p in self.root.iterdir()})
                # A fresh real exclusive lease must work after either successful
                # factory close or a rejected factory/check/backup. No lock mocks.
                with Lease(self.directory, exclusive=True) as lease:
                    self.assertTrue(lease.valid())
                self.assertEqual(lease_baseline, self._installed_manifest_source())

    def test_installed_manifest_integer_one_preserves_canonical_and_noncanonical_bytes(self):
        self.init()
        original = (self.directory/'installation.json').read_bytes()
        expected = manifest(load_config(self.config))
        self.assertIs(type(expected['format']), int)
        self.assertEqual(original, canonical(expected))
        reordered = dict(reversed(list(expected.items())))
        noncanonical = ('\n' + json.dumps(reordered, indent=2) + '\n').encode('ascii')
        self.assertNotEqual(noncanonical, original)
        for case, raw in (('canonical', original), ('noncanonical', noncanonical)):
            self._assert_installed_manifest_entrypoints(raw, case=case)

    def test_installed_manifest_format_types_and_versions_reject_all_entrypoints(self):
        self.init()
        expected = manifest(load_config(self.config))
        for case, value in (('boolean', True), ('float', 1.0), ('string', '1'),
                            ('null', None), ('zero-version', 0), ('future-version', 2)):
            raw = canonical(dict(expected, format=value))
            self._assert_installed_manifest_entrypoints(raw, case=case, error='unsupported_layout')

    def test_installed_manifest_exact_shape_and_values_reject_all_entrypoints(self):
        self.init()
        expected = manifest(load_config(self.config))
        cases = [('missing-' + key, {k: v for k, v in expected.items() if k != key}) for key in expected]
        cases.append(('extra-field', dict(expected, extra='PRIVATE_MANIFEST_MARKER')))
        cases.extend(('wrong-' + key, dict(expected, **{key: 'PRIVATE_MANIFEST_MARKER'}))
                     for key in ('binding', 'policy_schema', 'login_schema'))
        cases.extend((('top-list', []), ('top-string', 'PRIVATE_MANIFEST_MARKER'),
                      ('top-number', 1), ('top-null', None)))
        for case, value in cases:
            self._assert_installed_manifest_entrypoints(canonical(value), case=case, error='unsupported_layout')

    def test_installed_manifest_decode_failures_keep_existing_entrypoint_errors(self):
        self.init()
        original = (self.directory/'installation.json').read_bytes()
        # A real duplicate in raw JSON: constructing a Python dict would lose it.
        duplicate = original[:-1] + b',"format":1}'
        cases = (('invalid-json', b'{"PRIVATE_MANIFEST_MARKER":'),
                 ('duplicate-key', duplicate), ('invalid-utf8', b'\xff'),
                 ('nonfinite-constant', original.replace(b'"format":1', b'"format":NaN')))
        for case, raw in cases:
            self._assert_installed_manifest_entrypoints(raw, case=case, error='invalid_configuration')


if __name__=='__main__':unittest.main()
