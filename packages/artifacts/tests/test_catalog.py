import unittest
from datetime import datetime
from packages.artifacts import (Artifact, ArtifactCatalog, Scope, ScopeUnavailable,
                               Snapshot, quarter_for_time, Condition, Definition, SavedView)

SCOPE = Scope('alpha', 'storage', 'builds')
OBSERVED = '2026-04-02T12:00:00Z'
NOW = '2026-04-01T01:00:00Z'


def artifact(key='release/tool.tar', **kwargs):
    return Artifact(key, kwargs.pop('modified_at', NOW), **kwargs)


def catalog(*records, scope=SCOPE, **kwargs):
    return ArtifactCatalog([Snapshot(scope, OBSERVED, records, **kwargs)])


class TimeTests(unittest.TestCase):
    def test_all_quarters(self):
        for month in range(1, 13):
            self.assertEqual(quarter_for_time(f'2026-{month:02}-01T00:00:00Z'), f'2026Q{(month-1)//3+1}')

    def test_timezone_boundary_explicit(self):
        self.assertEqual(quarter_for_time('2026-03-31T20:00:00Z'), '2026Q1')
        self.assertEqual(quarter_for_time('2026-03-31T20:00:00Z', offset_minutes=480), '2026Q2')
        self.assertEqual(quarter_for_time('2026-04-01T01:00:00+08:00'), '2026Q1')

    def test_naive_and_invalid_times_rejected(self):
        for value in ('2026-04-01', '2026-04-01T01:00:00', '2026-02-30T00:00:00Z', None, 4, ''):
            with self.subTest(value=value), self.assertRaises(ValueError):
                quarter_for_time(value)

    def test_bad_offset_rejected(self):
        for offset in (True, 841, -841, '480', None):
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                quarter_for_time(NOW, offset_minutes=offset)

    def test_overflow_rejected(self):
        with self.assertRaises(ValueError):
            quarter_for_time('9999-12-31T23:59:59Z', offset_minutes=840)


class InputTests(unittest.TestCase):
    def test_scope_fields_required(self):
        for values in (('', 'x', 'b'), ('a', '../c', 'b'), ('a', 'c', None)):
            with self.assertRaises(ValueError):
                Scope(*values)

    def test_marker_and_size_validation(self):
        for kwargs in ({'key': 'x/', 'is_dir': False}, {'key': 'x', 'is_dir': True},
                       {'size': True}, {'size': -1}, {'is_dir': 1},
                       {'key': 'x/', 'is_dir': True, 'size': 3}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                artifact(**kwargs)

    def test_path_rejections(self):
        for path in ('../x', 'a/../b', '/root', 'https://example.test/x', 'a\\b', 'a\x00b', '\ud800'):
            with self.subTest(path=repr(path)), self.assertRaises(ValueError):
                artifact(path)

    def test_duplicate_and_future_records_rejected(self):
        with self.assertRaises(ValueError):
            catalog(artifact(), artifact())
        with self.assertRaises(ValueError):
            catalog(artifact(modified_at='2026-04-03T00:00:00Z'))

    def test_invalid_input_collection(self):
        for values in ('x', [None], [artifact()] * 10001):
            with self.assertRaises(ValueError):
                Snapshot(SCOPE, OBSERVED, values)
        with self.assertRaises(ValueError):
            ArtifactCatalog([None])

    def test_copied_immutable_inputs(self):
        data = [artifact()]
        snapshot = Snapshot(SCOPE, OBSERVED, data)
        data.clear()
        self.assertEqual(len(snapshot.artifacts), 1)
        with self.assertRaises(AttributeError):
            snapshot.artifacts = ()


class CatalogTests(unittest.TestCase):
    def test_parents_generated_per_quarter(self):
        model = catalog(artifact('old/deep/a', modified_at='2026-03-31T12:00:00Z'), artifact('new/b'))
        rows = model.query(SCOPE, '2026Q1', Definition())['items']
        self.assertEqual({r['key'] for r in rows}, {'old/', 'old/deep/', 'old/deep/a'})
        self.assertTrue(next(r for r in rows if r['key'] == 'old/')['virtual'])
        self.assertEqual(model.quarters(SCOPE), ('2026Q2', '2026Q1'))

    def test_directory_marker_wins_over_virtual_parent(self):
        model = catalog(artifact('x/a'), artifact('x/', is_dir=True))
        folder = model.query(SCOPE, '2026Q2', Definition())['items'][0]
        self.assertFalse(folder['virtual'])
        self.assertEqual(folder['modified_at'], NOW)

    def test_literal_repeated_segments_preserved(self):
        rows = catalog(artifact('a//b')).query(SCOPE, '2026Q2', Definition())['items']
        self.assertEqual({r['key'] for r in rows}, {'a/', 'a//', 'a//b'})
        leaf = next(r for r in rows if not r['is_dir'])
        self.assertEqual(leaf['parent'], 'a//')

    def test_one_level_and_recursive(self):
        model = catalog(artifact('a/b/c'), artifact('a/d'), artifact('ab/e'))
        direct = model.query(SCOPE, '2026Q2', Definition(path='a/', recursive=False))
        self.assertEqual({r['key'] for r in direct['items']}, {'a/b/', 'a/d'})
        recursive = model.query(SCOPE, '2026Q2', Definition(path='a/', files_only=True))
        self.assertEqual({r['key'] for r in recursive['items']}, {'a/b/c', 'a/d'})
        with self.assertRaises(ValueError):
            Definition(path='a')  # directory prefixes must have a segment boundary

    def test_root_listing(self):
        rows = catalog(artifact('x'), artifact('a/b')).query(SCOPE, '2026Q2', Definition(recursive=False))['items']
        self.assertEqual([r['key'] for r in rows], ['a/', 'x'])

    def test_missing_quarter_explicitly_empty(self):
        page = catalog(artifact()).query(SCOPE, '2025Q4', Definition())
        self.assertEqual(page['items'], [])
        self.assertFalse(page['quarter_available'])
        self.assertEqual(page['observed_at'], OBSERVED)

    def test_empty_snapshot(self):
        page = catalog().query(SCOPE, '2026Q2', Definition())
        self.assertEqual(page['total'], 0)
        self.assertFalse(page['quarter_available'])

    def test_scope_is_exact_not_workspace_union(self):
        scopes = [SCOPE, Scope('beta', 'storage', 'builds'), Scope('alpha', 'other', 'builds'),
                  Scope('alpha', 'storage', 'other'), Scope('commons', 'storage', 'builds')]
        model = ArtifactCatalog([Snapshot(s, OBSERVED, (artifact(str(i)),)) for i, s in enumerate(scopes)])
        for i, scope in enumerate(scopes):
            page = model.query(scope, '2026Q2', Definition())
            self.assertEqual([r['key'] for r in page['items']], [str(i)])
            self.assertEqual(page['scope'], scope.as_dict())

    def test_unknown_scope_and_duplicate_fail(self):
        with self.assertRaises(ScopeUnavailable):
            catalog(artifact()).quarters(Scope('unknown', 'storage', 'builds'))
        snap = Snapshot(SCOPE, OBSERVED, ())
        with self.assertRaises(ValueError):
            ArtifactCatalog([snap, snap])

    def test_invalid_quarters_and_paging(self):
        model = catalog(artifact())
        for value in ('2026Q5', '../x', None, '026Q1'):
            with self.assertRaises(ValueError):
                model.query(SCOPE, value, Definition())
        for paging in ({'page': True}, {'page': 0}, {'page_size': 101}, {'page_size': 0}):
            with self.assertRaises(ValueError):
                model.query(SCOPE, '2026Q2', Definition(), **paging)

    def test_stable_ties_and_pagination(self):
        model = catalog(artifact('b/x', size=2), artifact('a/x', size=2), artifact('root', size=1))
        definition = Definition(files_only=True, sort_by='size', order='desc')
        pages = [model.query(SCOPE, '2026Q2', definition, page=i, page_size=1) for i in range(1, 5)]
        self.assertEqual([p['items'][0]['key'] for p in pages[:3]], ['b/x', 'a/x', 'root'])
        self.assertEqual(pages[3]['items'], [])
        self.assertTrue(all(p['total'] == 3 for p in pages))

    def test_sort_timestamp_uses_instant_not_string(self):
        model = catalog(artifact('a', modified_at='2026-04-01T02:00:00+02:00'), artifact('b'))
        self.assertEqual([r['key'] for r in model.query(SCOPE, '2026Q2', Definition(sort_by='modified_at'))['items']], ['a', 'b'])

    def test_checksum_is_data_not_implicitly_deleted(self):
        self.assertEqual(catalog(artifact('x.sha256')).query(SCOPE, '2026Q2', Definition())['total'], 1)

    def test_response_mutation_does_not_change_snapshot(self):
        model = catalog(artifact('x'))
        page = model.query(SCOPE, '2026Q2', Definition())
        page['scope']['workspace_id'] = 'other'
        page['items'][0]['key'] = 'other'
        self.assertEqual(model.query(SCOPE, '2026Q2', Definition())['items'][0]['key'], 'x')


class ViewTests(unittest.TestCase):
    def test_parser_defaults_and_unknown_keys(self):
        self.assertEqual(Definition.from_dict({}), Definition())
        for payload in ({'download_url': 'https://example.test'}, {'conditions': [{'keyword': 'x', 'script': 'y'}]},
                        {'recursive': 'false'}, {'sort_by': 'DROP TABLE x'}, {'order': 'other'},
                        {'conditions': [{'operator': 'OR', 'keyword': 'x'}]}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                Definition.from_dict(payload)

    def test_condition_bounds(self):
        for value in ('', '  ', 'x\n', 'x' * 257):
            with self.assertRaises(ValueError):
                Condition(value)
        with self.assertRaises(ValueError):
            Definition(conditions=[Condition('x')] * 11)

    def test_boolean_precedence_and_not(self):
        definition = Definition.from_dict({'files_only': True, 'conditions': [
            {'keyword': 'alpha'}, {'operator': 'OR', 'keyword': 'beta'},
            {'operator': 'AND NOT', 'keyword': 'debug'}]})
        model = catalog(*(artifact(k) for k in ('alpha-debug', 'beta', 'beta-debug', 'gamma')))
        self.assertEqual({r['key'] for r in model.query(SCOPE, '2026Q2', definition)['items']}, {'alpha-debug', 'beta'})

    def test_percent_underscore_quote_literal(self):
        model = catalog(*(artifact(k) for k in ('x%_quote\'', 'xxxquote', 'x%other')))
        definition = Definition(conditions=(Condition("%_quote'"),))
        self.assertEqual(model.query(SCOPE, '2026Q2', definition)['total'], 1)

    def test_or_does_not_bypass_path_time_or_scope(self):
        model = catalog(artifact('inside/x'), artifact('outside/beta'))
        definition = Definition(path='inside/', conditions=(Condition('absent'), Condition('beta', 'OR')))
        self.assertEqual(model.query(SCOPE, '2026Q2', definition)['items'], [])

    def test_time_bounds_inclusive_exclude_virtual(self):
        model = catalog(artifact('a/x'))
        definition = Definition(start_time=NOW, end_time=NOW)
        rows = model.query(SCOPE, '2026Q2', definition)['items']
        self.assertEqual([r['key'] for r in rows], ['a/x'])
        with self.assertRaises(ValueError):
            Definition(start_time=OBSERVED, end_time=NOW)

    def test_saved_view_scope_must_match_all_dimensions(self):
        definition = Definition(files_only=True)
        model = catalog(artifact('a/x'))
        view = SavedView(SCOPE, 'release', 'Release', definition)
        self.assertEqual(model.apply_view(SCOPE, '2026Q2', view)['total'], 1)
        for other in (Scope('beta', 'storage', 'builds'), Scope('alpha', 'other', 'builds'), Scope('alpha', 'storage', 'other')):
            with self.assertRaises(ValueError):
                model.apply_view(SCOPE, '2026Q2', SavedView(other, 'release', 'Release', definition))

    def test_saved_view_not_found_never_falls_back(self):
        with self.assertRaises(ValueError):
            catalog(artifact()).apply_view(SCOPE, '2026Q2', None)

    def test_saved_view_identity_validation(self):
        for name in ('', ' ', 'x' * 81, 'x\n'):
            with self.assertRaises(ValueError):
                SavedView(SCOPE, 'id', name, Definition())

    def test_conditions_are_immutable_copy(self):
        conditions = [Condition('x')]
        definition = Definition(conditions=conditions)
        conditions.clear()
        self.assertEqual(len(definition.conditions), 1)


if __name__ == '__main__':
    unittest.main()
