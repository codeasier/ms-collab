# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Bounded normalization of already acquired files pages. No live provider I/O."""
from dataclasses import dataclass,asdict
import re
from .core import repo_path

_SHA=re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})\Z')
_REASONS={'','missing_diff_identity','invalid_files_response','invalid_file_entry','pagination_limit',
          'invalid_pr_response','diff_changed','invalid_file_count','conflicting_file_counts','file_count_mismatch','files_unavailable'}


@dataclass(frozen=True)
class PathEvidence:
    head_sha:str
    base_sha:str
    base:str
    complete:bool
    paths:tuple[str,...]
    reason:str
    consistency:str='head_base_rechecked_unpinned'

    def __post_init__(self):
        if (type(self.complete) is not bool or type(self.paths) is not tuple or len(self.paths)>4000
                or type(self.reason) is not str or self.reason not in _REASONS
                or self.consistency!='head_base_rechecked_unpinned'):
            raise ValueError('invalid_path_evidence')
        for value in (self.head_sha,self.base_sha,self.base):
            if type(value) is not str or len(value)>256:raise ValueError('invalid_path_identity')
        if self.complete:
            identity({'head_sha':self.head_sha,'base_sha':self.base_sha,'base':self.base})
            if self.reason or len(set(self.paths))!=len(self.paths) or tuple(sorted(self.paths))!=self.paths:
                raise ValueError('invalid_complete_evidence')
            for path in self.paths:repo_path(path)
        elif self.paths or not self.reason:
            raise ValueError('partial_paths_not_allowed')

    def as_dict(self):return asdict(self)|{'paths':list(self.paths)}

    @classmethod
    def from_dict(cls,value):
        if type(value) is not dict or set(value)!={'head_sha','base_sha','base','complete','paths','reason','consistency'} or type(value['paths']) is not list:
            raise ValueError('invalid_path_evidence')
        return cls(**(value|{'paths':tuple(value['paths'])}))


def identity(raw):
    if type(raw) is not dict or set(raw)!={'head_sha','base_sha','base'}:raise ValueError('invalid_diff_identity')
    if any(type(raw[k]) is not str or not _SHA.fullmatch(raw[k]) for k in ('head_sha','base_sha')):raise ValueError('invalid_sha')
    branch=raw['base']
    if type(branch) is not str or not branch or len(branch)>256 or any(c.isspace() or ord(c)<32 or ord(c)==127 for c in branch):raise ValueError('invalid_branch')
    return dict(raw)


def file_paths(entry):
    if type(entry) is not dict:raise ValueError('invalid_file')
    filename=repo_path(entry.get('filename'));status=entry.get('status');previous=entry.get('previous_filename');patch=entry.get('patch')
    if status is not None and (type(status) is not str or status not in {'added','modified','removed','renamed'}):raise ValueError('invalid_status')
    if type(patch) is dict:
        old,new=repo_path(patch.get('old_path')),repo_path(patch.get('new_path'))
        flags=[patch.get(k) for k in ('new_file','renamed_file','deleted_file')]
        if any(not(type(f) is bool or type(f) is int and f in (0,1)) for f in flags):raise ValueError('invalid_flag')
        added,renamed,removed=map(bool,flags)
        derived='added' if added else 'renamed' if renamed else 'removed' if removed else 'modified'
        if (sum((added,renamed,removed))>1 or status is not None and status!=derived
                or filename!=new or renamed!=(old!=new) or previous is not None and (not renamed or repo_path(previous)!=old)):
            raise ValueError('inconsistent_file')
        return filename,{old,new}
    if patch is not None and type(patch) is not str:raise ValueError('invalid_patch')
    if status is None:raise ValueError('unknown_status')
    if status=='renamed':
        old=repo_path(previous)
        if old==filename:raise ValueError('invalid_rename')
        return filename,{old,filename}
    if previous is not None:raise ValueError('unexpected_rename')
    return filename,{filename}


def normalize_paths(expected, pages, rechecked):
    """A terminating page and matching final identity are NOT ABA-safe proof.

    The trusted acquirer supplies ordered pages and a detail recheck; this pure
    function does not fetch, authenticate or prove when those inputs were read.
    On every invalid/incomplete case no partial paths survive.
    """
    try:expected=identity(expected)
    except (ValueError,TypeError):return PathEvidence('','','',False,(),'missing_diff_identity')
    def fail(reason):return PathEvidence(**expected,complete=False,paths=(),reason=reason)
    if type(pages) not in {tuple,list} or not pages:return fail('files_unavailable')
    if len(pages)>20:return fail('pagination_limit')
    paths=set();filenames=set()
    for index,page in enumerate(pages):
        if type(page) is not list or len(page)>100 or index<len(pages)-1 and len(page)!=100:return fail('invalid_files_response')
        try:
            for entry in page:
                filename,values=file_paths(entry)
                if filename in filenames:raise ValueError()
                filenames.add(filename);paths.update(values)
        except (ValueError,TypeError):return fail('invalid_file_entry')
    if len(pages[-1])==100:return fail('pagination_limit')
    if type(rechecked) is not dict or not {'head_sha','base_sha','base'}<=rechecked.keys():return fail('invalid_pr_response')
    if any(rechecked[k]!=v for k,v in expected.items()):return fail('diff_changed')
    counts=[]
    for key in ('changed_files','changes_count','files_changed'):
        if key not in rechecked:continue
        value=rechecked[key]
        if type(value) is int and value>=0:counts.append(value)
        elif type(value) is str and re.fullmatch(r'(?:0|[1-9][0-9]{0,19})',value):counts.append(int(value))
        else:return fail('invalid_file_count')
    if len(set(counts))>1:return fail('conflicting_file_counts')
    if counts and counts[0]!=len(filenames):return fail('file_count_mismatch')
    return PathEvidence(**expected,complete=True,paths=tuple(sorted(paths)),reason='')
