# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Reference comparisons with SQLite LIKE, without importing the OBS project."""
import itertools
import sqlite3
import unittest

from packages.artifacts.product_conditions import ASCII_FOLD, compile_conditions, matches_conditions


class ProductConditions(unittest.TestCase):
    def test_term_matching_agrees_with_sqlite_like(self):
        # SQLite's default LIKE folds only ASCII; '_' matches a Unicode code point.
        # Overlapping pieces, repeated wildcards and regex punctuation are important.
        names = ['aab', 'abaB', 'AAAbb', '中文a', 'a%b', 'a_b', 'éß', 'Éss', 'aa/xx',
                 'A[xy]Z', '(a).{2}', 'AéäB', 'a\U0001f600B', '[]?$^+']
        patterns = [''.join(x) for x in itertools.product('a%_b', repeat=3)]
        patterns += ['É', 'é', 'ß', 'SS', '中_', 'a_B', 'a__B', '[xy]', '(a).{2}', '/%',
                     '_[x_]_', '%%%a%%', '[]?$^+', 'a_%a', '%a%a%', ' a ']
        with sqlite3.connect(':memory:') as db:
            for pattern in patterns:
                group = compile_conditions([{'operator':'包含', 'keyword':pattern}])
                for name in names:
                    with self.subTest(pattern=pattern, name=name):
                        expected, = db.execute('SELECT ? LIKE ?', (name, '%'+pattern.strip()+'%')).fetchone()
                        self.assertEqual(matches_conditions(name.translate(ASCII_FOLD), group), bool(expected))

    def test_eight_terms_exact_bound_and_precedence_not_ltr_or_global_exclusion(self):
        parts = [('包含', 'alpha'), ('OR','beta'), ('AND','gamma'), ('AND NOT','delta'),
                 ('OR','second'), ('AND','x'), ('OR','third'), ('AND NOT','last')]
        group = compile_conditions([{'operator':op, 'keyword':kw} for op, kw in parts])
        for key, value in [('alpha_delta', True), ('beta_only', False), ('gamma', False),
                           ('gamma-beta-delta', False), ('beta-gamma', True), ('second-x-last', True),
                           ('second', False), ('third', True), ('third-last', False)]:
            self.assertEqual(matches_conditions(key, group), value)
