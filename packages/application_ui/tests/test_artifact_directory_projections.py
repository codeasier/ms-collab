# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Real cookie/SQLite regressions for bounded directory byte projections."""
from dataclasses import replace
from pathlib import Path
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from packages.application_host.tests import fixtures
from packages.application_ui.tests.artifact_fixtures import application, SCOPE
from packages.application_ui.tests.artifact_discovery_fixtures import seed_directory
from packages.application_ui.tests.test_application import request
from packages.artifact_sync import Connection, IndexPolicy, initialize_indexes, records
from packages.artifact_sync.tests.authority_fixtures import seed_publication
from packages.artifacts import Artifact, Scope, Snapshot
from packages.session_api.application import _SESSION, _CSRF
from packages.session_api.tests.test_application import cookie_header


class ArtifactDirectoryProjectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app, self.policy, self.login, self.exchange = application(self.tmp.name)
        self.path = Path(self.tmp.name) / 'policy.sqlite'
        session = fixtures.session(self.login, self.exchange)
        self.cookies = cookie_header({_SESSION: session.token.value, _CSRF: session.csrf.value})

    def sql(self, sql, args=()):
        with sqlite3.connect(self.path) as db:
            return db.execute(sql, args).fetchall()

    def call(self, *, managed=False):
        namespace = 'artifact-admin' if managed else 'artifacts'
        return request(self.app, f'/api/workspaces/alpha/{namespace}/scopes', cookies=self.cookies)

    def exact(self, action):
        namespace = 'artifacts' if action == 'quarters' else 'artifact-admin'
        return request(self.app, f'/api/workspaces/alpha/{namespace}/source/bucket/{action}',
                       cookies=self.cookies, revision='"2"')

    def raw(self, field):
        return self.sql(f"SELECT {field} FROM artifact_indexes_v1 WHERE workspace='alpha' AND connection='source' AND bucket='bucket'")[0][0]

    def set_raw(self, field, raw):
        # CAST permits synthetic malformed UTF-8 in a STRICT TEXT column.
        self.sql(f"UPDATE artifact_indexes_v1 SET {field}=CAST(? AS TEXT) WHERE workspace='alpha' AND connection='source' AND bucket='bucket'", (raw,))

    def unavailable(self, result):
        self.assertEqual(result['status'], 503)
        self.assertEqual(result['body'], {'error': 'storage_unavailable'})

    def test_canonical_policy_publication_candidate_with_nul_tail_matches_exact_failure(self):
        reader = self.call()
        self.assertEqual(reader['status'], 200)
        self.assertEqual(len(reader['body']['data']['items']), 1)
        self.assertEqual(self.call(managed=True)['status'], 200)
        for field in ('policy', 'published', 'candidate'):
            original = self.raw(field)
            with self.subTest(field=field):
                # In particular, the original publication proof still matches
                # the policy prefix, but must not authenticate a truncated value.
                self.set_raw(field, original + '\0synthetic-trailing-record')
                if field == 'candidate':
                    self.assertEqual(self.call()['body'], reader['body'])
                    self.assertEqual(self.exact('quarters')['status'], 200)
                else:
                    self.unavailable(self.call())
                    self.unavailable(self.exact('quarters'))
                self.unavailable(self.call(managed=True))
                for action in ('status', 'review'):
                    self.unavailable(self.exact(action))
            self.set_raw(field, original)

    def test_nul_key_alone_never_becomes_empty_success_or_aliases_a_valid_key(self):
        for field, valid in (('connection', 'source'), ('bucket', 'bucket')):
            for keep_valid in (False, True):
                with self.subTest(field=field, keep_valid=keep_valid):
                    bad = valid + '\0synthetic-key-tail'
                    if keep_valid:
                        columns = ('workspace', 'connection', 'bucket', 'revision', 'policy',
                                   'candidate', 'published', 'published_revision')
                        projection = ','.join('?' if name == field else name for name in columns)
                        self.sql(f"INSERT INTO artifact_indexes_v1 SELECT {projection} FROM artifact_indexes_v1 WHERE workspace='alpha'", (bad,))
                    else:
                        self.sql(f"UPDATE artifact_indexes_v1 SET {field}=? WHERE workspace='alpha'", (bad,))
                    for managed in (False, True):
                        self.unavailable(self.call(managed=managed))
                    # An alias must neither duplicate nor replace the real scope.
                    self.assertEqual(self.exact('quarters')['status'], 200 if keep_valid else 404)
                    if keep_valid:
                        self.sql(f"DELETE FROM artifact_indexes_v1 WHERE workspace='alpha' AND {field}=?", (bad,))
                    else:
                        self.sql(f"UPDATE artifact_indexes_v1 SET {field}=? WHERE workspace='alpha'", (valid,))

    def test_invalid_utf8_and_split_multibyte_record_prefixes_fail_value_free(self):
        for field in ('policy', 'published', 'candidate'):
            original = self.raw(field)
            encoded = original.encode('ascii')
            corruptions = (
                encoded + b'\xff',
                encoded + b'\xc3',
                encoded + b' ' * (records._MAX_BYTES - len(encoded)) + '\u20ac'.encode('utf-8'),
            )
            for number, raw in enumerate(corruptions):
                with self.subTest(field=field, corruption=number):
                    self.set_raw(field, raw)
                    self.unavailable(self.call(managed=True))
                    if field != 'candidate':
                        self.unavailable(self.call())
                    else:
                        self.assertEqual(self.call()['status'], 200)
            self.set_raw(field, original)

    def test_noncanonical_multibyte_json_is_not_reencoded_into_success(self):
        for field in ('policy', 'published', 'candidate'):
            original = self.raw(field)
            value = json.loads(original)
            if field == 'policy':
                value['policy']['connection']['prefix'] = '\u00e9/'
            elif field == 'published':
                value['snapshot']['artifacts'][1]['key'] = 'builds/\u00e9.txt'
                value['provenance']['snapshot_digest'] = records.digest(records.dump(value['snapshot']))
            else:
                value['artifacts'][1]['key'] = 'builds/\u00e9.txt'
            raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            self.assertGreater(len(raw.encode('utf-8')), len(raw))
            self.set_raw(field, raw)
            with self.subTest(field=field):
                self.unavailable(self.call(managed=True))
                if field != 'candidate':
                    self.unavailable(self.call())
            self.set_raw(field, original)

    def test_invalid_key_bytes_and_multibyte_split_fail_before_exact_lookup(self):
        for field, valid in (('connection', 'source'), ('bucket', 'bucket')):
            for raw in (valid.encode() + b'\xff', valid.encode() + '\u00e9'.encode(),
                        b'x' * 128 + '\u20ac'.encode()):
                with self.subTest(field=field, raw_length=len(raw)):
                    self.sql(f"UPDATE artifact_indexes_v1 SET {field}=CAST(? AS TEXT) WHERE workspace='alpha'", (raw,))
                    for managed in (False, True):
                        self.unavailable(self.call(managed=managed))
                    self.sql(f"UPDATE artifact_indexes_v1 SET {field}=? WHERE workspace='alpha'", (valid,))

    def test_reader_still_selects_null_and_never_decodes_private_candidate(self):
        before = self.call()['body']
        self.set_raw('candidate', b'\xffsynthetic-private-candidate\0tail')
        projections = []
        connect = self.policy._connect
        def traced():
            db = connect()
            db.set_trace_callback(projections.append)
            return db
        with patch.object(self.policy, '_connect', side_effect=traced):
            self.assertEqual(self.call()['body'], before)
        selects = [sql for sql in projections if 'SELECT revision,' in sql]
        self.assertTrue(selects)
        self.assertTrue(all(',NULL,' in sql and 'candidate' not in sql for sql in selects))
        self.assertTrue(all('substr(CAST(policy AS BLOB),1,1048577)' in sql and
                            'substr(CAST(published AS BLOB),1,1048577)' in sql for sql in selects))
        self.unavailable(self.call(managed=True))

    def test_denied_payload_bytes_are_not_decoded_or_charged(self):
        seed_directory(self.policy)
        before = [self.call(managed=managed)['body'] for managed in (False, True)]
        hidden = b'\xffsynthetic-denied-private-payload\0tail'
        self.sql("UPDATE artifact_indexes_v1 SET candidate=CAST(? AS TEXT),published=CAST(? AS TEXT) WHERE workspace='alpha' AND bucket='hidden'", (hidden, hidden))
        decode = records._decode_projection
        def checked(raw):
            self.assertNotEqual(raw, hidden, 'denied payload must not be decoded')
            return decode(raw)
        with patch.object(records, '_decode_projection', side_effect=checked):
            for managed, body in zip((False, True), before):
                self.assertEqual(self.call(managed=managed)['body'], body)

    def sized_snapshot(self, size):
        # Unicode keys stay within 1024 UTF-8 bytes each, but canonical ASCII
        # escapes let this valid 500-object snapshot reach the real 1 MiB limit.
        stamp = '2026-01-01T00:00:00Z'
        rows = [Artifact(f'{i:03d}-', stamp, 1) for i in range(500)]
        snapshot = Snapshot(SCOPE, stamp, rows)
        remaining = size - len(records.snapshot_text(snapshot))
        for index, row in enumerate(rows):
            count = min(500, remaining // 6)
            extra = remaining % 6 if count < 500 else 0
            rows[index] = replace(row, key=row.key + '\u00e9' * count + 'x' * extra)
            remaining -= count * 6 + extra
        self.assertEqual(remaining, 0)
        raw = records.snapshot_text(replace(snapshot, artifacts=tuple(rows)))
        self.assertTrue(raw.isascii())
        self.assertEqual(len(raw.encode('utf-8')), size)
        return raw

    def test_real_one_mib_canonical_record_and_single_byte_overflow_sentinel(self):
        originals = {field: self.raw(field) for field in ('candidate', 'published')}
        proof = json.loads(originals['published'])['provenance']
        # The fixed-size digest leaves the wrapper overhead independent of data.
        empty = records.snapshot_text(Snapshot(SCOPE, '2026-01-01T00:00:00Z', ()))
        overhead = len(records.publication_text(empty, {**proof, 'snapshot_digest': records.digest(empty)})) - len(empty)
        for field in ('candidate', 'published'):
            raw = self.sized_snapshot(records._MAX_BYTES - (overhead if field == 'published' else 0))
            if field == 'published':
                raw = records.publication_text(raw, {**proof, 'snapshot_digest': records.digest(raw)})
            self.assertEqual(len(raw), records._MAX_BYTES)
            with self.subTest(field=field):
                self.set_raw(field, raw)
                self.assertEqual(self.call(managed=True)['status'], 200)
                self.assertEqual(self.exact('status')['status'], 200)
                self.assertEqual(self.call()['status'], 200)
                for tail in (' ', '\0', ' ' * 4096):
                    self.set_raw(field, raw + tail)
                    with patch.object(records, '_decode_projection', wraps=records._decode_projection) as decode:
                        self.unavailable(self.call(managed=True))
                    lengths = [len(call.args[0]) for call in decode.call_args_list]
                    self.assertEqual(max(lengths), records._MAX_BYTES + 1)
                    self.unavailable(self.exact('status'))
                    if field == 'published':
                        self.unavailable(self.call())
                    else:
                        self.assertEqual(self.call()['status'], 200)
            self.set_raw(field, originals[field])

    def test_real_128_byte_keys_remain_valid_and_129th_byte_is_not_lost(self):
        scope = Scope('alpha', 'c' * 128, 'b' * 128)
        initialize_indexes(self.policy, [IndexPolicy(Connection(scope, 'synthetic-bucket', 'eu-west-101'),
            'synthetic-reference', ('admin',), ('admin',), ('admin',))])
        seed_publication(self.policy, scope, Snapshot(scope, '2026-01-01T00:00:00Z', ()))
        for managed in (False, True):
            result = self.call(managed=managed)
            self.assertEqual(result['status'], 200)
            self.assertIn(scope.as_dict(), [item['scope'] for item in result['body']['data']['items']])
        for field, valid in (('connection', scope.connection_id), ('bucket', scope.bucket_id)):
            self.sql(f"UPDATE artifact_indexes_v1 SET {field}=? WHERE workspace='alpha' AND {field}=?", (valid + 'x', valid))
            for managed in (False, True):
                self.unavailable(self.call(managed=managed))
            self.sql(f"UPDATE artifact_indexes_v1 SET {field}=? WHERE workspace='alpha' AND {field}=?", (valid, valid + 'x'))
