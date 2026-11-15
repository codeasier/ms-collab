# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Pure named-filter contract. A saved/shared view never grants access."""
from dataclasses import dataclass
from .catalog import Scope, key, text, instant


@dataclass(frozen=True, slots=True)
class Condition:
    keyword: str
    operator: str = 'AND'

    def __post_init__(self):
        text(self.keyword, 256)
        if not self.keyword.strip() or self.operator not in ('AND', 'OR', 'AND NOT'):
            raise ValueError('invalid condition')
        object.__setattr__(self, 'keyword', self.keyword.strip())


@dataclass(frozen=True, slots=True)
class Definition:
    path: str = ''
    recursive: bool = True
    conditions: tuple[Condition, ...] = ()
    start_time: str | None = None
    end_time: str | None = None
    files_only: bool = False
    sort_by: str = 'name'
    order: str = 'asc'

    def __post_init__(self):
        key(self.path, directory=True, root=True)
        if type(self.recursive) is not bool or type(self.files_only) is not bool:
            raise ValueError('query flags must be boolean')
        if not isinstance(self.conditions, (list, tuple)) or len(self.conditions) > 10:
            raise ValueError('expected at most 10 conditions')
        values = tuple(self.conditions)
        if any(not isinstance(c, Condition) for c in values) or (values and values[0].operator != 'AND'):
            raise ValueError('first condition must be AND')
        object.__setattr__(self, 'conditions', values)
        start = instant(self.start_time) if self.start_time is not None else None
        end = instant(self.end_time) if self.end_time is not None else None
        if start and end and start > end:
            raise ValueError('reversed time range')
        if self.sort_by not in ('name', 'size', 'modified_at') or self.order not in ('asc', 'desc'):
            raise ValueError('invalid sort')

    def matches(self, row):
        if self.files_only and row.is_dir:
            return False
        if self.recursive:
            if not row.key.startswith(self.path):
                return False
        elif row.parent != self.path:
            return False
        if self.start_time is not None or self.end_time is not None:
            if row.modified_at is None:
                return False
            when = instant(row.modified_at)
            if self.start_time is not None and when < instant(self.start_time):
                return False
            if self.end_time is not None and when > instant(self.end_time):
                return False
        # Preserve SQL AND-before-OR grouping, but use literal case-sensitive
        # substrings. %, _ and quotes are data, never wildcard or SQL syntax.
        group, groups = True, []
        for condition in self.conditions:
            found = condition.keyword in row.key
            if condition.operator == 'OR':
                groups.append(group)
                group = found
            else:
                group = group and (not found if condition.operator == 'AND NOT' else found)
        return any((*groups, group))

    @classmethod
    def from_dict(cls, payload):
        allowed = {'path', 'recursive', 'conditions', 'start_time', 'end_time',
                   'files_only', 'sort_by', 'order'}
        if not isinstance(payload, dict) or set(payload) - allowed:
            raise ValueError('unknown view definition field')
        data = dict(payload)
        conditions = data.pop('conditions', [])
        if not isinstance(conditions, list) or len(conditions) > 10:
            raise ValueError('invalid conditions')
        parsed = []
        for condition in conditions:
            if not isinstance(condition, dict) or set(condition) - {'keyword', 'operator'} or 'keyword' not in condition:
                raise ValueError('invalid condition fields')
            parsed.append(Condition(**condition))
        return cls(conditions=tuple(parsed), **data)


@dataclass(frozen=True, slots=True)
class SavedView:
    scope: Scope
    view_id: str
    name: str
    definition: Definition

    def __post_init__(self):
        if not isinstance(self.scope, Scope) or not isinstance(self.definition, Definition):
            raise ValueError('scope and definition required')
        text(self.view_id, 128)
        text(self.name, 80)
        if not self.view_id.strip() or not self.name.strip():
            raise ValueError('empty view identity')
