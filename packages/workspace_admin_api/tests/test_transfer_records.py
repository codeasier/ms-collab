"""Historical receipt codec/storage only; no HTTP replay or live-authority claim."""
from contextlib import closing
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from packages.workspaces import StorageError, SQLiteWorkspaceService
from packages.workspaces.model import MAX_REVISION
from packages.workspace_admin_api import transfer_records as records
from packages.service_runtime.tests.layout_profile_fixture import (
    FIELDS, append_transfer, canonical, create_policy, key, load_policy,
    projected, record, vector)

# Independently transcribed canonical contract, frozen without importing the
# production encoder. Hashes below are hashes of these literal ASCII bytes.
GOLDEN_VECTOR = (
    b'{"action":"admin.transfer","actor":{"authority_epoch":1,"generation":1,"id":"admin",'
    b'"issuer":"synthetic","membership":{"authority":{"principals":{"admin":{"generation":1,'
    b'"issuer":"synthetic","subject":"admin"}},"realm":"00000000000000000000000000000000",'
    b'"version":1},"revision":1,"role":"admin","state":"active"},"subject":"admin"},'
    b'"realm":"00000000000000000000000000000000","replacement":{"authority_epoch":1,"generation":1,'
    b'"id":"member","issuer":"synthetic","membership":{"authority":{"principals":{"member":'
    b'{"generation":1,"issuer":"synthetic","subject":"member"}},"realm":"00000000000000000000000000000000",'
    b'"version":1},"revision":1,"role":"developer","state":"active"},"subject":"member"},'
    b'"route":"/api/workspaces/alpha/admin-transfers","version":1,"workspace":{"id":"alpha",'
    b'"revision":1,"state":"active"}}')
GOLDEN_INTENT = (
    b'{"action":"admin.transfer","actor":"admin","actor_revision":1,"precondition":'
    b'"ats1-8f98d508033978b5331374c65e8e04a6fa37a3f044149bb2d0cd52ad4cc9be78",'
    b'"replacement":"member","replacement_revision":1,"version":1,"workspace":"alpha"}')
GOLDEN_DIGEST = '25a3a9e85689c891b5ba6bda493dc2abb0d4a569fe3d8c4011a91796fe637458'


class TransferCodecTests(unittest.TestCase):
    def assert_invalid(self, value):
        with self.assertRaises(StorageError) as error:
            records.decode_record(projected(value))
        self.assertEqual(error.exception.code, 'invalid_transfer_record')
        self.assertEqual(str(error.exception), 'invalid_transfer_record')

    def test_literal_golden_vector_request_and_digest(self):
        self.assertEqual(len(GOLDEN_VECTOR), 811)
        self.assertEqual(sha256(GOLDEN_VECTOR).hexdigest(),
                         '8f98d508033978b5331374c65e8e04a6fa37a3f044149bb2d0cd52ad4cc9be78')
        self.assertEqual(sha256(GOLDEN_INTENT).hexdigest(), GOLDEN_DIGEST)
        self.assertEqual(canonical(vector()), GOLDEN_VECTOR)
        original = {'actor': 'admin', 'workspace_id': 'alpha',
            'request_key': '00000000-0000-4000-8000-000000000001',
            'replacement_id': 'member', 'intent': GOLDEN_INTENT.decode('ascii'),
            'request_digest': GOLDEN_DIGEST, 'provenance': GOLDEN_VECTOR.decode('ascii'),
            'actor_revision': 1, 'replacement_revision': 1, 'audit_first': 1, 'audit_last': 2}
        self.assertEqual(record(vector()), original)
        decoded = records.decode_record(projected(original))
        self.assertEqual(decoded, dict(original, intent=json.loads(GOLDEN_INTENT),
                                      provenance=json.loads(GOLDEN_VECTOR)))
        self.assertEqual(records.FIELDS, FIELDS)
        self.assertEqual((records.INTENT_LIMIT, records.VECTOR_LIMIT, records.QUOTA, records.BATCH),
                         (4096, 32768, 256, 64))

    def test_non_bmp_maximum_identity_vector_exceeds_old_16k_without_truncation(self):
        non_bmp = '\U0001f680' * 200
        review = vector(actor=non_bmp, replacement='r' * 200, workspace='w' * 200,
            issuer=non_bmp, actor_subject=non_bmp, replacement_subject='\U0001f680' * 199 + '\U0001f681',
            actor_revision=MAX_REVISION - 1, replacement_revision=MAX_REVISION - 1,
            generation=MAX_REVISION, authority_epoch=MAX_REVISION, workspace_revision=MAX_REVISION)
        saved = record(review, audit_first=MAX_REVISION - 1)
        raw = canonical(review)
        self.assertGreater(len(raw), 16384)
        self.assertLessEqual(len(raw), 32725)  # Reviewed conservative exact-shape bound.
        self.assertLessEqual(len(saved['intent'].encode('ascii')), 4096)
        self.assertEqual(len(non_bmp.encode('utf-8')), 800)
        self.assertEqual(records.decode_record(projected(saved))['provenance'], review)
        self.assertEqual(records.decode_record(projected(saved))['actor'], non_bmp)

    def test_policy_controls_and_shared_prefix_nul_are_preserved_not_login_validated(self):
        review = vector(actor='admin\x00suffix\x7f', issuer='issuer\x00retained\x7f',
                        actor_subject='subject\x00actor', replacement_subject='subject\x00recipient')
        raw = canonical(review)
        self.assertIn(b'\\u0000', raw)
        self.assertNotIn(b'\x00', raw)
        decoded = records.decode_record(projected(record(review)))
        self.assertEqual(decoded['actor'], 'admin\x00suffix\x7f')
        self.assertNotEqual(decoded['actor'], 'admin')
        self.assertEqual(decoded['provenance'], review)
        # Historical identity strings are policy IDs, not issuer URLs.
        self.assertEqual(decoded['provenance']['actor']['issuer'], 'issuer\x00retained\x7f')

    def test_already_admin_replacement_is_a_valid_historical_core_outcome(self):
        saved = record(vector(replacement_role='admin'))
        self.assertEqual(records.decode_record(projected(saved))['provenance']['replacement']['membership']['role'], 'admin')

    def test_raw_types_utf8_and_complete_blob_sentinels_reject(self):
        good = record(vector())
        for field, value in (
                ('actor', b'\xff'), ('actor', b'a' * 801), ('actor', 'a' * 201),
                ('actor', 'white space'), ('workspace_id', b'w' * 201),
                ('replacement_id', b'r' * 201), ('workspace_id', '\u00e9'),
                ('request_key', good['request_key'] + '\x00tail'),
                ('request_key', good['request_key'].upper().replace('0000', 'AAAA', 1)),
                ('request_digest', good['request_digest'] + '\x00'),
                ('intent', b'x' * 4097), ('provenance', b'x' * 32769),
                ('intent', b'\xff'), ('provenance', b'\xc3\xa9')):
            with self.subTest(field=field, value=repr(value)[:60]):
                self.assert_invalid(dict(good, **{field: value}))
        row = projected(good)
        for invalid in (list(row), row[:-1], row + (1,),
                        (good['actor'], *row[1:]), (None, *row[1:])):
            with self.assertRaises(StorageError):
                records.decode_record(invalid)

    def test_exact_integer_domains_include_max_post_but_no_bool_float_or_overflow(self):
        good = record(vector())
        for field in ('actor_revision', 'replacement_revision', 'audit_first', 'audit_last'):
            for value in (True, False, 1.0, '1', 0, -1, MAX_REVISION + 1):
                with self.subTest(field=field, value=value):
                    self.assert_invalid(dict(good, **{field: value}))
        for field in ('actor_revision', 'replacement_revision', 'audit_first'):
            with self.subTest(field=field, boundary='no-increment-room'):
                self.assert_invalid(dict(good, **{field: MAX_REVISION}))
        self.assert_invalid(dict(good, audit_last=3))
        saved = record(vector(actor_revision=MAX_REVISION - 1,
                              replacement_revision=MAX_REVISION - 1), audit_first=MAX_REVISION - 1)
        self.assertEqual(records.decode_record(projected(saved))['audit_last'], MAX_REVISION)

    def test_intent_exact_shape_counters_duplicate_columns_and_proof_binding(self):
        good = record(vector())
        intent = json.loads(good['intent'])
        changes = [{k: v for k, v in intent.items() if k != field} for field in intent]
        changes += [dict(intent, extra=1)]
        changes += [dict(intent, **{field: value}) for field, value in (
            ('version', True), ('version', 1.0), ('version', 2), ('action', 'membership.update'),
            ('actor', 'other'), ('workspace', 'other'), ('replacement', 'other'),
            ('actor_revision', 2), ('replacement_revision', 2), ('actor_revision', True),
            ('replacement_revision', 1.0), ('precondition', 'ats1-' + '0' * 64),
            ('precondition', intent['precondition'] + '\x00'))]
        for changed in changes:
            raw = canonical(changed)
            with self.subTest(intent=changed):
                # Repair the outer hash deliberately, so semantic crosschecks
                # rather than only the digest must detect the corruption.
                self.assert_invalid(dict(good, intent=raw, request_digest=sha256(raw).hexdigest()))
        for field, value in (('actor', 'other'), ('workspace_id', 'other'),
                ('replacement_id', 'other'), ('actor_revision', 2), ('replacement_revision', 2),
                ('request_digest', '0' * 64)):
            self.assert_invalid(dict(good, **{field: value}))

    def test_historical_vector_exact_nested_shapes_pins_and_types(self):
        original = vector()
        changes = []
        for label, mapping in (('vector', original), ('workspace', original['workspace']),
                ('actor', original['actor']), ('membership', original['actor']['membership'])):
            for field in mapping:
                changed = deepcopy(original)
                target = {'vector': changed, 'workspace': changed['workspace'],
                          'actor': changed['actor'], 'membership': changed['actor']['membership']}[label]
                del target[field]
                changes.append(changed)
        for path, value in (
                (('version',), True), (('version',), 1.0), (('version',), 2),
                (('route',), '/api/workspaces/other/admin-transfers'), (('realm',), 'G' * 32),
                (('workspace', 'state'), 'suspended'), (('workspace', 'revision'), True),
                (('actor', 'generation'), 1.0), (('actor', 'authority_epoch'), 0),
                (('actor', 'membership', 'role'), 'developer'),
                (('actor', 'membership', 'state'), 'revoked'),
                (('replacement', 'membership', 'state'), 'revoked'),
                (('actor', 'membership', 'authority'), None),
                (('actor', 'membership', 'authority', 'realm'), 'f' * 32),
                (('actor', 'membership', 'authority', 'principals', 'admin', 'subject'), 'different'),
                (('actor', 'membership', 'authority', 'principals', 'admin', 'generation'), True),
                (('actor', 'issuer'), 'has space'), (('actor', 'subject'), '\ud800')):
            changed = deepcopy(original)
            target = changed
            for part in path[:-1]:
                target = target[part]
            target[path[-1]] = value
            changes.append(changed)
        changed = deepcopy(original)
        changed['actor']['extra'] = 1
        changes.append(changed)
        for index, changed in enumerate(changes):
            # The fixture normally derives duplicate intent fields. Keep the
            # original columns where malformed shape has removed such fields.
            raw = canonical(changed)
            intent = json.loads(GOLDEN_INTENT)
            intent['precondition'] = 'ats1-' + sha256(raw).hexdigest()
            intent_raw = canonical(intent)
            saved = record(original)
            saved.update(provenance=raw, intent=intent_raw,
                         request_digest=sha256(intent_raw).hexdigest())
            with self.subTest(case=index):
                self.assert_invalid(saved)

    def test_canonical_json_envelopes_reject_duplicate_tail_nul_float_and_nonfinite(self):
        good = record(vector())
        for field in ('intent', 'provenance'):
            raw = good[field].encode('ascii')
            mutations = (raw + b'\x00', b' ' + raw, raw + b'\n',
                         raw[:-1] + b',"version":1}',
                         raw.replace(b'"version":1', b'"version":1.0', 1),
                         raw.replace(b'"version":1', b'"version":NaN', 1),
                         b'null', b'[]', b'"text"')
            for changed in mutations:
                with self.subTest(field=field, raw=changed[-50:]):
                    self.assert_invalid(dict(good, **{field: changed}))


class TransferStorageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'policy.sqlite'

    def populate(self, store, workspace, count, *, start=1):
        """Explicit trusted role changes, never silent pin/identity rewriting."""
        saved = []
        for index in range(count):
            saved.append(append_transfer(store, workspace=workspace, number=start + index))
            if index + 1 < count:
                # A separate real inverse core operation makes the original
                # actor admin again. It has audit events but no synthetic receipt.
                revision = 2 * (index + 1)
                store.transfer_admin('member', workspace, 'admin',
                    expected_revision=revision, replacement_revision=revision)
        return saved

    def validate(self):
        with closing(sqlite3.connect(self.path)) as db:
            changes = db.total_changes
            result = records.validate_all(db, load_policy(db))
            self.assertEqual(db.total_changes, changes)
            return result

    def test_real_core_pair_survives_restart_without_requiring_current_admin(self):
        store = create_policy(self.path)
        saved = append_transfer(store)
        before = self.path.read_bytes()
        restarted = SQLiteWorkspaceService(self.path)
        with closing(sqlite3.connect(self.path)) as db:
            policy = load_policy(db)
            self.assertEqual(policy._memberships[('alpha', 'admin')].role, 'developer')
            self.assertEqual(records.validate_all(db, policy), 1)
            decoded = records.decode_record(db.execute(records.RECEIPT_SQL,
                ('admin', 'alpha', saved['request_key'])).fetchone())
            self.assertEqual(decoded['intent'], json.loads(saved['intent']))
            self.assertEqual([(a.action, a.object_id, a.revision) for a in policy._audit],
                             [('membership.promote', 'member', 2), ('membership.demote', 'admin', 2)])
        self.assertIsInstance(restarted, SQLiteWorkspaceService)
        self.assertEqual(self.path.read_bytes(), before)

    def test_blob_projection_preserves_nul_actor_and_audit_object_full_identity(self):
        actor = 'same\x00retained\x7f'
        store = create_policy(self.path, actor=actor, issuer='issuer\x00policy',
                              actor_subject='subject\x00actor', replacement_subject='subject\x00recipient')
        saved = append_transfer(store, actor=actor)
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute(records.RECEIPT_SQL, (actor, 'alpha', saved['request_key'])).fetchone()
            self.assertEqual(row[0], actor.encode('utf-8'))
            self.assertEqual(records.decode_record(row)['actor'], actor)
            self.assertIsNone(db.execute(records.RECEIPT_SQL, ('same', 'alpha', saved['request_key'])).fetchone())
            self.assertEqual(records.validate_all(db, load_policy(db)), 1)
            # A shared prefix is not a match for the full stored actor/object.
            db.execute('UPDATE audit SET object_id=? WHERE action=?', ('same', 'membership.demote'))
            with self.assertRaises(StorageError):
                records.validate_all(db, load_policy(db))

    def test_schema_rejects_extra_family_index_trigger_and_temp_alias_without_ddl(self):
        create_policy(self.path)
        for statement in (
                'CREATE TABLE workspace_admin_transfers_v2(x TEXT)',
                'CREATE INDEX unexpected ON workspace_admin_transfers_v1(replacement_id)',
                'CREATE TRIGGER unexpected AFTER INSERT ON workspace_admin_transfers_v1 BEGIN SELECT 1; END',
                'CREATE TEMP TABLE workspace_admin_transfers_v1(x TEXT)'):
            with closing(sqlite3.connect(self.path)) as db:
                db.execute('BEGIN')
                db.execute(statement)
                with self.assertRaises(StorageError) as error:
                    records.check_schema(db)
                self.assertEqual(error.exception.code, 'invalid_transfer_schema')
                db.rollback()
                records.check_schema(db)
        self.assertEqual(self.validate(), 0)

    def test_every_page_and_partition_is_validated_and_late_corruption_is_seen(self):
        store = create_policy(self.path, workspace_ids=('alpha', 'beta', 'gamma'))
        self.populate(store, 'alpha', 65)
        self.populate(store, 'beta', 65, start=1000)
        last = self.populate(store, 'gamma', 3, start=2000)[-1]
        before = self.path.read_bytes()
        self.assertEqual(self.validate(), 133)
        self.assertEqual(self.path.read_bytes(), before)
        with sqlite3.connect(self.path) as db:
            db.execute('UPDATE workspace_admin_transfers_v1 SET request_digest=? WHERE request_key=?',
                       ('0' * 64, last['request_key']))
        with self.assertRaises(StorageError):
            self.validate()

    def test_quota_256_spans_pages_and_257_is_rejected_without_eviction(self):
        store = create_policy(self.path, workspace_ids=('alpha', 'beta'))
        self.populate(store, 'alpha', 256)
        self.populate(store, 'beta', 65, start=1000)
        self.assertEqual(self.validate(), 321)
        # Explicit inverse operation for a new legal transfer by the same actor.
        store.transfer_admin('member', 'alpha', 'admin',
                             expected_revision=512, replacement_revision=512)
        append_transfer(store, workspace='alpha', number=257)
        before = self.path.read_bytes()
        with self.assertRaises(StorageError):
            self.validate()
        self.assertEqual(before, self.path.read_bytes())
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM workspace_admin_transfers_v1').fetchone()[0], 322)

    def test_audit_pair_fields_and_missing_reference_are_not_only_outer_hash_checks(self):
        store = create_policy(self.path)
        append_transfer(store)
        mutations = (
            ("UPDATE audit SET actor_id='member' WHERE sequence=1", ()),
            ("UPDATE audit SET action='membership.demote' WHERE sequence=1", ()),
            ("UPDATE audit SET object_id='admin' WHERE sequence=1", ()),
            ('UPDATE audit SET revision=3 WHERE sequence=1', ()),
            ('DELETE FROM audit WHERE sequence=2', ()),
            ('UPDATE workspace_admin_transfers_v1 SET audit_first=3,audit_last=4', ()),
            ('UPDATE workspace_admin_transfers_v1 SET actor_revision=2', ()))
        for sql, parameters in mutations:
            with self.subTest(sql=sql), closing(sqlite3.connect(self.path)) as db:
                policy = load_policy(db)
                db.execute('BEGIN')
                db.execute(sql, parameters)
                with self.assertRaises(StorageError):
                    records.validate_all(db, policy)
                db.rollback()
                self.assertEqual(records.validate_all(db, load_policy(db)), 1)
        with closing(sqlite3.connect(self.path)) as db:
            for kind, ident in (('_principals', 'admin'), ('_principals', 'member'), ('_workspaces', 'alpha')):
                policy = load_policy(db)
                del getattr(policy, kind)[ident]
                with self.assertRaises(StorageError):
                    records.validate_all(db, policy)

    def test_unique_audit_linkage_cannot_back_two_retained_receipts(self):
        store = create_policy(self.path)
        saved = append_transfer(store)
        with closing(sqlite3.connect(self.path)) as db:
            duplicate = dict(saved, request_key=key(2))
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute('INSERT INTO workspace_admin_transfers_v1 VALUES (' +
                           ','.join('?' for _ in FIELDS) + ')', tuple(duplicate[f] for f in FIELDS))
            db.rollback()
            self.assertEqual(records.validate_all(db, load_policy(db)), 1)

    def test_history_ignores_today_generation_realm_revocation_and_null_ownership(self):
        store = create_policy(self.path)
        append_transfer(store)
        with closing(sqlite3.connect(self.path)) as db:
            policy = load_policy(db)
            policy._principals['admin'] = replace(policy._principals['admin'],
                                                  active=False, generation=2, authority_epoch=2)
            policy._realm = 'f' * 32
            old = policy._memberships[('alpha', 'admin')]
            policy._memberships[('alpha', 'admin')] = replace(old, state='revoked', revision=old.revision + 1)
            policy._membership_authority[('alpha', 'admin')] = None
            with patch('packages.workspaces.authority.authority_current', side_effect=AssertionError('not current auth')):
                self.assertEqual(records.validate_all(db, policy), 1)

    def test_actual_sql_keyset_and_lookup_plans_have_bounded_projection_and_vm_work(self):
        store = create_policy(self.path, workspace_ids=('alpha', 'beta'))
        first = self.populate(store, 'alpha', 65)
        self.populate(store, 'beta', 65, start=1000)
        with closing(sqlite3.connect(self.path)) as db:
            selected = first[63]
            after = ('admin', 'alpha', selected['request_key'])
            for sql, parameters, expected_rows in (
                    (records.RECEIPT_SQL, after, 1),
                    (records.FIRST_PAGE_SQL, (), records.BATCH + 1),
                    (records.NEXT_PAGE_SQL, after, records.BATCH + 1)):
                details = [row[3].upper() for row in db.execute('EXPLAIN QUERY PLAN ' + sql, parameters)]
                self.assertFalse(any('TEMP B-TREE' in item for item in details), details)
                self.assertTrue(any('INDEX' in item for item in details), details)
                if sql != records.FIRST_PAGE_SQL:
                    self.assertTrue(any('SEARCH' in item for item in details), details)
                steps = [0]
                def progress():
                    steps[0] += 1
                    return int(steps[0] > 10000)
                db.set_progress_handler(progress, 1)
                try:
                    rows = db.execute(sql, parameters).fetchall()
                finally:
                    db.set_progress_handler(None, 0)
                self.assertEqual(len(rows), expected_rows)
                self.assertLess(steps[0], 10000)
                self.assertTrue(all(type(row[0]) is bytes and len(row[0]) <= 801 for row in rows))
            # Even malformed giant raw fields are projected to their sentinel,
            # never materialized as an unbounded SELECT * by the codec path.
            db.execute('UPDATE workspace_admin_transfers_v1 SET provenance=? WHERE request_key=?',
                       ('x' * 100000, selected['request_key']))
            bounded = db.execute(records.RECEIPT_SQL, after).fetchone()
            self.assertEqual(len(bounded[6]), records.VECTOR_LIMIT + 1)
            with self.assertRaises(StorageError):
                records.decode_record(bounded)


if __name__ == '__main__':
    unittest.main()
