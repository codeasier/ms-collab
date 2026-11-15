# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
import copy
import unittest
from packages.responsibility import fields,repo_path,PathEvidence,normalize_paths,project
from packages.community_board.model import BoardItem

DIFF={'head_sha':'a'*40,'base_sha':'b'*40,'base':'main'}
FIELDS=[dict(id='ui',name='UI',kind='developer',paths=['ui'],owner_ids=['alice','bob'],backup={'principal_id':'carol','field_id':'core'}),
        dict(id='core',name='Core',kind='developer',paths=['src'],owner_ids=['carol'],backup=None),
        dict(id='review',name='Committer',kind='committer',paths=['ui','src'],owner_ids=['reviewer'],backup=None)]

def proof(paths):return normalize_paths(DIFF,[[{'filename':p,'status':'modified'} for p in paths]],DIFF)

class CoreTests(unittest.TestCase):
    def test_config_order_and_backup_are_preserved_without_aliases(self):
        raw=copy.deepcopy(FIELDS);got=fields(raw);self.assertEqual(got,FIELDS)
        raw[0]['owner_ids'].reverse();raw[0]['backup']['principal_id']='bad'
        self.assertEqual(got[0]['owner_ids'],['alice','bob']);self.assertEqual(got[0]['backup']['principal_id'],'carol')

    def test_config_strict_identity_and_closed_shape(self):
        for patch in ({'id':'../bad'},{'name':''},{'kind':'owner'},{'owner_ids':['alice','alice']},
                      {'owner_ids':['alice','bob','carol','fourth']},{'owner_ids':[1]},
                      {'paths':['ui','ui']},{'paths':[]},{'secret':'unexpected'},{'owner_ids':['alice\n']}):
            raw=copy.deepcopy(FIELDS);raw[0].update(patch)
            with self.subTest(patch=patch),self.assertRaises((ValueError,TypeError)):fields(raw)
        for value in (None,{},[FIELDS[0],FIELDS[0]]):
            with self.assertRaises(ValueError):fields(value)

    def test_backup_is_cross_developer_owner_only(self):
        for backup in ({'principal_id':'alice','field_id':'ui'},{'principal_id':'carol','field_id':'missing'},
                       {'principal_id':'reviewer','field_id':'review'},{'principal_id':'bob','field_id':'core'},
                       {'user_id':'carol','field_id':'core'}):
            raw=copy.deepcopy(FIELDS);raw[0]['backup']=backup
            with self.assertRaises(ValueError):fields(raw)
        raw=copy.deepcopy(FIELDS);raw[2]['backup']={'principal_id':'carol','field_id':'core'}
        with self.assertRaises(ValueError):fields(raw)

    def test_literal_path_validation_and_no_glob(self):
        for value in ('','/root','a//b','a/../b','a/./b','a\\b','a/','a\n','a\x7f'):
            with self.assertRaises(ValueError):repo_path(value)
        for value in ('ui','ui/a','wild*','中文/文件','file with space'):self.assertEqual(repo_path(value),value)
        config=[dict(id='literal',name='Literal',kind='developer',paths=['wild*'],owner_ids=['alice'],backup=None)]
        item=BoardItem('pull',1,'P','open','2026-01-01T00:00:00Z')
        result=project(config,[item],{item.key:proof(['wild/file'])},'alice',view='all')
        self.assertEqual(result['items'][0]['fields'],[])

    def test_overlap_partial_coverage_and_boundary(self):
        pull=BoardItem('pull',2,'Pull','open','2026-01-01T00:00:00Z')
        result=project(FIELDS,[pull],{pull.key:proof(['ui/a','ui-extra/b','src/c'])},'alice')
        row=result['items'][0]
        self.assertEqual(row['assignment_status'],'partially_mapped');self.assertEqual(row['unmapped_paths'],['ui-extra/b'])
        self.assertEqual([f['id'] for f in row['fields']],['ui','core','review']);self.assertEqual(row['my_field_ids'],['ui'])
        self.assertFalse(row['approval_available']);self.assertFalse(result['workflow_enabled']);self.assertFalse(result['transport_executable'])

    def test_indexed_prefix_matching_large_config_preserves_order_and_literal_semantics(self):
        config=[dict(id=f'f{i}',name=f'Field {i}',kind='developer' if i%2 else 'committer',
                     paths=[f'group{i}/area{j}' for j in range(100)],owner_ids=['alice'],backup=None)
                for i in range(200)]
        item=BoardItem('pull',1,'Synthetic large path set','open','2026-01-01T00:00:00Z')
        paths=tuple(sorted([f'group{i}/area99/file' for i in range(200)]+['group1/area99-extra/file','unknown/file']))
        observation=PathEvidence(**DIFF,complete=True,paths=paths,reason='')
        row=project(config,[item],{item.key:observation},'alice',view='all')['items'][0]
        self.assertEqual([f['id'] for f in row['fields']],[f'f{i}' for i in range(200)])
        self.assertEqual(row['my_field_ids'],[f'f{i}' for i in range(200) if i%2])
        self.assertEqual(row['unmapped_paths'],[p for p in paths if p in {'group1/area99-extra/file','unknown/file'} or int(p.split('/')[0][5:])%2==0])

    def test_backup_committer_and_issue_ids_do_not_imply_assignment(self):
        issue=BoardItem('issue',1,'Issue','open','2026-01-01T00:00:00Z');pull=BoardItem('pull',2,'Pull','open','2026-01-01T00:00:00Z')
        for actor in ('carol','reviewer'):
            self.assertEqual(project(FIELDS,[issue,pull],{pull.key:proof(['ui/a'])},actor)['items'],[])
        self.assertEqual(project(FIELDS,[issue],{},'alice',view='unassigned')['items'][0]['assignee_identity_status'],'unknown')
        self.assertEqual(project(FIELDS,[issue,pull],{pull.key:proof(['ui/a'])},'alice',view='field',field_id='review')['total'],1)

    def test_incomplete_paths_have_no_partial_assignment(self):
        pull=BoardItem('pull',2,'Pull','open','2026-01-01T00:00:00Z')
        bad=normalize_paths(DIFF,[[{'filename':'ui/a','status':'modified'},None]],DIFF)
        self.assertFalse(bad.complete);self.assertEqual(bad.paths,())
        result=project(FIELDS,[pull],{pull.key:bad},'alice',view='all')
        self.assertEqual(result['incomplete_pull_count'],1);self.assertEqual(result['items'][0]['assignment_status'],'incomplete')
        self.assertEqual(result['items'][0]['fields'],[])

    def test_pagination_is_stable_filtered_and_closed_items_excluded(self):
        items=[BoardItem('issue',i,str(i),'open' if i<4 else 'closed','2026-01-01T00:00:00Z') for i in (4,3,2,1)]
        got=project(FIELDS,items,{},'alice',view='all',offset=1,limit=1)
        self.assertEqual((got['total'],got['items'][0]['number']),(3,2))
        for options in ({'view':'bad'},{'view':'field','field_id':'missing'},{'view':'all','field_id':'ui'},{'limit':0},{'offset':True}):
            with self.assertRaises(ValueError):project(FIELDS,items,{},'alice',**options)

    def test_rename_status_includes_both_paths(self):
        got=normalize_paths(DIFF,[[{'filename':'ui/new','previous_filename':'src/old','status':'renamed'}]],DIFF|{'changed_files':1})
        self.assertTrue(got.complete);self.assertEqual(got.paths,('src/old','ui/new'))
        self.assertEqual(got.consistency,'head_base_rechecked_unpinned')

    def test_patch_flag_shape_requires_consistency(self):
        entry={'filename':'ui/new','status':None,'patch':{'old_path':'src/old','new_path':'ui/new','new_file':0,'renamed_file':True,'deleted_file':False}}
        self.assertTrue(normalize_paths(DIFF,[[entry]],DIFF).complete)
        for change in ({'status':'modified'},{'previous_filename':'wrong'},{'filename':'different'}):
            self.assertFalse(normalize_paths(DIFF,[[entry|change]],DIFF).complete)
        for key,value in [('new_file',1),('renamed_file','true'),('old_path','ui/new')]:
            patched=entry|{'patch':entry['patch']|{key:value}}
            self.assertFalse(normalize_paths(DIFF,[[patched]],DIFF).complete)

    def test_page_termination_duplicates_and_partial_failures(self):
        full=[{'filename':f'ui/{i}','status':'modified'} for i in range(100)]
        self.assertTrue(normalize_paths(DIFF,[full,[]],DIFF|{'changed_files':100}).complete)
        for pages in ([full],[[full[0]],[]],[full,[full[0]]],[full,None],[[{'filename':'ui/a'}]],None,[]):
            got=normalize_paths(DIFF,pages,DIFF);self.assertFalse(got.complete);self.assertEqual(got.paths,())
        self.assertEqual(normalize_paths(DIFF,[full]*21,DIFF).reason,'pagination_limit')

    def test_diff_identity_count_aliases_and_explicit_unknown(self):
        entry={'filename':'ui/a','status':'added'}
        self.assertTrue(normalize_paths(DIFF,[[entry]],DIFF|{'changed_files':'1','changes_count':1}).complete)
        for detail,reason in [(DIFF|{'changed_files':None},'invalid_file_count'),(DIFF|{'changes_count':True},'invalid_file_count'),
                              (DIFF|{'changed_files':1,'files_changed':2},'conflicting_file_counts'),
                              (DIFF|{'changed_files':0},'file_count_mismatch'),(DIFF|{'base_sha':'c'*40},'diff_changed'),({},'invalid_pr_response')]:
            self.assertEqual(normalize_paths(DIFF,[[entry]],detail).reason,reason)
        self.assertEqual(normalize_paths({},[[entry]],DIFF).reason,'missing_diff_identity')

    def test_path_evidence_roundtrip_and_incomplete_cannot_carry_paths(self):
        p=proof(['src/a']);self.assertEqual(PathEvidence.from_dict(p.as_dict()),p)
        for patch in ({'paths':['src/a','src/a']},{'consistency':'atomic_commit'},{'complete':False,'reason':'files_unavailable'},{'head_sha':'unknown'}):
            with self.assertRaises(ValueError):PathEvidence.from_dict(p.as_dict()|patch)
