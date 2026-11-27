# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Bounded, ordered OBS advanced conditions on already scoped relative keys."""
import re

from .catalog import text

ASCII_FOLD = str.maketrans('ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')


def compile_conditions(value):
    """First include, then AND / OR / AND NOT. No arbitrary expression tree."""
    if type(value) is not list or not 1 <= len(value) <= 8:
        raise ValueError('invalid product conditions')
    groups = [[]]
    for index, item in enumerate(value):
        if type(item) is not dict or item.keys() != {'operator', 'keyword'}:
            raise ValueError('invalid product condition')
        op = item['operator']
        if type(op) is not str or op not in (('包含',) if index == 0 else ('AND', 'OR', 'AND NOT')):
            raise ValueError('invalid product operator')
        keyword = text(item['keyword'], 100).strip()
        if not keyword:
            raise ValueError('empty product condition')
        # SQL LIKE '%keyword%': % separates ordered, non-overlapping pieces;
        # _ consumes exactly one Unicode code point. Never compile user regex
        # or join pieces with .* (backtracking on repeated wildcards).
        pieces = []
        for piece in keyword.translate(ASCII_FOLD).split('%'):
            if '_' in piece:
                pieces.append(re.compile('.'.join(re.escape(p) for p in piece.split('_')), re.DOTALL))
            elif piece:
                pieces.append(piece)
        if op == 'OR':
            groups.append([])
        groups[-1].append((op == 'AND NOT', tuple(pieces)))
    return tuple(tuple(group) for group in groups)


def _like(folded_key, pieces):
    pos = 0
    for piece in pieces:
        if isinstance(piece, str):
            offset = folded_key.find(piece, pos)
            if offset < 0:
                return False
            pos = offset + len(piece)
        else:
            match = piece.search(folded_key, pos)
            if match is None:
                return False
            pos = match.end()
    return True


def matches_conditions(folded_key, groups):
    """SQLite precedence: OR separates AND groups, with NOT on its own term."""
    return any(all(_like(folded_key, pieces) != negated for negated, pieces in group)
               for group in groups)
