# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Hand-counted synthetic examples. Nothing here grants access to a snapshot."""
import unittest
from unittest.mock import patch

from packages.artifacts import Artifact, Scope, Snapshot
from packages.artifacts.product_projection import project


SCOPE = Scope('lab', 'idx', 'internal-01')
STAMP = '2026-04-02T02:03:04Z'
SEEN = '2026-08-01T00:00:00Z'
OTHER_QUARTER = '2026-03-02T02:03:04Z'
MATCH = dict(format=1, quarter='2026Q2')


def file(key, at=STAMP, size=7):
    return Artifact(key, at, size)


def marker(key):
    return Artifact(key, STAMP, 0, True)


def snapshot(*rows, offset=0):
    return Snapshot(SCOPE, SEEN, rows, offset)


def level():
    return snapshot(file('pkg/Kit_alpha'), file('pkg/Kit_alpha/z.bin'),
                    file('pkg/a//KiT_one.zip'), file('pkg/ab/KIT_two.zip'),
                    file('pkg/notes-KIT_false.txt'), file('pkg/ab/Other.bin'),
                    file('pkg/only-side/KIT_s.SHA256'), marker('pkg/empty-candidate/KIT_marker/'),
                    file('pkg/KIT_not-ascii.txt'), file('pkg/kitXone.txt'),
                    marker('pkg/Kit_alpha/'), file('pkg/older/KIT_q1', OTHER_QUARTER))


def ask(snap, token='kit_', **selection):
    return project(snap, token, {**MATCH, **selection})


def keys(result):
    return [row['key'] for row in result['items']]


class ProductProjectionTests(unittest.TestCase):
    def test_tree_projects_only_next_segment_from_surviving_real_leaves(self):
        snap = level()
        with patch.object(Snapshot, 'entries', side_effect=AssertionError('not the legacy full tree')):
            root = ask(snap)
            children = ask(snap, path='pkg/')
        self.assertEqual((root['path'], root['mode'], root['page'], root['page_size'], root['total']),
                         ('', 'tree', 1, 50, 1))
        self.assertEqual(keys(root), ['pkg/'])
        self.assertEqual(children['total'], 5)
        self.assertEqual(keys(children), ['pkg/Kit_alpha/', 'pkg/a/', 'pkg/ab/',
                                          'pkg/Kit_alpha', 'pkg/notes-KIT_false.txt'])
        self.assertEqual(children['items'][0], dict(key='pkg/Kit_alpha/', name='Kit_alpha',
                         parent='pkg/', modified_at=None, size=0, is_dir=True, virtual=True))
        self.assertEqual(children['items'][3], dict(key='pkg/Kit_alpha', name='Kit_alpha',
                         parent='pkg/', modified_at=STAMP, size=7, is_dir=False, virtual=False))
        self.assertEqual(keys(ask(snap, path='pkg/Kit_alpha/')), ['pkg/Kit_alpha/z.bin'])
        self.assertEqual(keys(ask(snap, path='pkg/a/')), ['pkg/a//'])
        self.assertEqual(ask(snap, path='pkg/a/')['items'][0]['name'], '')
        self.assertEqual(keys(ask(snap, path='pkg/a//')), ['pkg/a//KiT_one.zip'])
        self.assertEqual(keys(ask(snap, path='pkg/ab/')), ['pkg/ab/KIT_two.zip'])
        self.assertEqual(keys(ask(snap, path='PKG/')), [])

    def test_deduplicated_sorted_rows_are_paginated_without_name_collision(self):
        snap = level()
        pages = [['pkg/Kit_alpha/', 'pkg/a/'], ['pkg/ab/', 'pkg/Kit_alpha'],
                 ['pkg/notes-KIT_false.txt'], []]
        for page, expected in enumerate(pages, 1):
            with self.subTest(page=page):
                result = ask(snap, path='pkg/', page=page, page_size=2, if_observation='0'*64)
                self.assertEqual((result['total'], keys(result)), (5, expected))

    def test_files_repeat_the_mandatory_leaf_filter_before_literal_refinements(self):
        snap = level()
        files = dict(path='pkg/', mode='files')
        self.assertEqual(keys(ask(snap, **files)), ['pkg/Kit_alpha', 'pkg/notes-KIT_false.txt'])
        all_keys = keys(ask(snap, recursive=True, **files))
        self.assertEqual(all_keys, ['pkg/ab/KIT_two.zip', 'pkg/a//KiT_one.zip', 'pkg/Kit_alpha',
                                   'pkg/notes-KIT_false.txt', 'pkg/Kit_alpha/z.bin'])
        for word, wanted in [('Other', []), ('false', ['pkg/notes-KIT_false.txt']),
                             ('kIt_', []), ('one.zip', ['pkg/a//KiT_one.zip'])]:
            self.assertEqual(keys(ask(snap, keyword=word, recursive=True, **files)), wanted)

    def test_ascii_sidecar_suffix_ancestral_token_and_identical_basenames(self):
        snap = snapshot(file('KIT_a/same'), file('KIT_b/same'), file('kit_x.sHa256'),
                        file('kit_x.sha256.txt'), file('dir.sha256/KiT_y'),
                        file('KIT_a/same'), file('plain/file'))
        self.assertEqual(keys(ask(snap, mode='files', recursive=True)),
                         ['dir.sha256/KiT_y', 'kit_x.sha256.txt', 'KIT_a/same', 'KIT_b/same'])
        for token in ('internal-01', 'lab', 'idx', '7'):
            self.assertEqual(ask(snap, token)['total'], 0)

    def test_paths_and_keywords_preserve_punctuation_spaces_and_unicode(self):
        root = "测/%_'/<tag>/%2F/"
        literal = root + 'KIT_ x .txt'
        near = ("测/%X'/<tag>/%2F/KIT_x", "测/%_'/<tag>///KIT_x", root+'KIT_x.txt')
        snap = snapshot(file(literal), *(file(k) for k in near))
        self.assertEqual(keys(ask(snap, path=root, mode='files', keyword=' x ')), [literal])
        self.assertEqual(keys(ask(snap, path='测/', mode='files', recursive=True, keyword="%_'/<tag>/%2F/")),
                         [literal, root+'KIT_x.txt'])
        self.assertEqual(keys(ask(snap, path='测/%/', mode='tree')), [])

    def test_offset_quarter_and_closed_instant_range_do_not_imply_recursion(self):
        before = '2026-03-31T15:59:59Z'
        boundary = '2026-03-31T16:00:00Z'
        after = '2026-03-31T16:00:00.000001Z'
        local = '2026-04-01T00:00:00+08:00'
        snap = snapshot(file('pkg/KIT_1', before), file('pkg/KIT_2', boundary),
                        file('pkg/KIT_3', after), file('pkg/deeper/KIT_4', boundary), offset=480)
        files = dict(path='pkg/', mode='files')
        for refinement, wanted in [
            (dict(start_time=local, end_time=boundary), ['pkg/KIT_2']),
            (dict(start_time=local, end_time=boundary, recursive=True), ['pkg/KIT_2', 'pkg/deeper/KIT_4']),
            (dict(start_time=after), ['pkg/KIT_3']),
            (dict(end_time=boundary), ['pkg/KIT_2']),
            (dict(start_time=before, end_time=after), ['pkg/KIT_2', 'pkg/KIT_3']),
            (dict(quarter='2026Q1', end_time=local), ['pkg/KIT_1']),
            (dict(start_time=after, keyword='KIT_2'), []),
        ]:
            with self.subTest(refinement=refinement):
                self.assertEqual(keys(ask(snap, **files, **refinement)), wanted)

    def test_quarter_availability_is_about_stored_records_not_product_hits(self):
        snap = snapshot(file('other/KIT_guard.SHA256'), marker('KIT_empty/'))
        for quarter, available in [('2026Q2', True), ('2026Q3', False)]:
            result = ask(snap, quarter=quarter)
            self.assertIs(result['quarter_available'], available)
            self.assertEqual((result['total'], result['items']), (0, []))

    def test_index_budget_validates_all_rows_including_nonmatches(self):
        largest = 'kit_' + 'é'*510  # exactly 1024 UTF-8 bytes
        deep = 'a/'*31 + 'KIT_leaf'  # exactly 32 segments
        rows = tuple(file('KIT_%03d' % n) for n in range(498)) + (file(largest), file(deep))
        result = ask(snapshot(*rows), mode='files', recursive=True, page_size=100)
        self.assertEqual((result['total'], len(result['items'])), (500, 100))
        self.assertEqual(keys(ask(snapshot(file(deep)), path='a/'*31)), [deep])
        independent_limits = [
            (*rows, file('other')),
            (file('é'*512+'x'),), (file('d/'*32+'x'),),
            (file('other', size=2**53),),
            tuple(file(str(i)+'😀'*250) for i in range(400)),  # >1 MiB canonical ASCII index
        ]
        for rows in independent_limits:
            with self.subTest(count=len(rows)), self.assertRaises(ValueError):
                ask(snapshot(*rows))

    def test_pure_entry_revalidates_token_and_exact_request(self):
        snap = level()
        for token in ['', ' kit', 'kit/', 'Kit', 'x'*65, 'kit\n', True]:
            with self.subTest(token=token), self.assertRaises(ValueError):
                ask(snap, token)
        for override in [dict(page=True), dict(mode='tree', recursive=False),
                         dict(path='a/'*32), dict(path='é'*512+'/'), dict(format=False),
                         dict(mode='files', keyword='  '), dict(unrecognized=False)]:
            with self.subTest(override=override), self.assertRaises(ValueError):
                ask(snap, **override)
