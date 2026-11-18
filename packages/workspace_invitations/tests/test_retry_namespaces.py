# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Synthetic namespace regressions through the real Bearer and Cookie seams."""
import unittest
from uuid import uuid4

from packages.workspace_admin_api.tests import retry_namespace_fixture as f


class InvitationRetryNamespaceTests(unittest.TestCase):
    def response(self, case, result, status=200, error=None):
        self.assertEqual(result['status'], status, result)
        headers = dict(result['headers'])
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(headers['Vary'],
                         'Cookie, Origin' if case.boundary == 'cookie' else 'Authorization')
        self.assertNotIn('WWW-Authenticate', headers)
        if error is not None:
            self.assertEqual(result['body'], {'error': error})
            return None
        self.assertEqual(set(result['body']), {'data'})
        return result['body']['data']

    def invitation(self, case, result, workspace, *, target='outsider', role='viewer'):
        item = self.response(case, result)
        self.assertEqual(set(item), {'id', 'workspace_id', 'workspace_name', 'principal_id',
                                    'role', 'state', 'created', 'expires', 'revision'})
        self.assertIsInstance(item['id'], str)
        self.assertTrue(item['id'])
        self.assertEqual((item['workspace_id'], item['workspace_name'], item['principal_id'],
                          item['role'], item['state'], item['revision']),
                         (workspace, workspace.title(), target, role, 'pending', 1))
        self.assertEqual(item['expires'] - item['created'], 86400)
        return item

    def records(self, case, expected):
        """Check the complete set of namespaces and their exact response IDs."""
        rows = case.sql(
            f'SELECT workspace_id,inviter,request_key,id,principal_id,role,state,revision,'
            f'created,expires,workspace_revision,inviter_revision,member_revision '
            f'FROM {f.INVITATION_TABLE} ORDER BY workspace_id,inviter,request_key')
        self.assertEqual(rows, sorted(
            (workspace, actor, key, item['id'], item['principal_id'], item['role'],
             item['state'], item['revision'], item['created'], item['expires'], 1, 1, 0)
            for workspace, actor, key, item in expected))
        # Invitation retries have their own ledger, not admin_requests_v2 provenance.
        self.assertEqual(case.admin_rows(), [])

    def target_memberships(self, case):
        return case.sql("SELECT workspace_id,principal_id,role,state,revision FROM memberships "
                        "WHERE principal_id IN ('viewer','outsider') "
                        "ORDER BY workspace_id,principal_id")

    def test_same_key_is_inviter_scoped_without_bypassing_target_uniqueness(self):
        for boundary in f.BOUNDARIES:
            with self.subTest(boundary=boundary), f.RetryNamespaceFixture(boundary) as case:
                key = str(uuid4())
                admin = self.invitation(case, case.invite('alpha', key=key), 'alpha')
                before = case.snapshot()
                # No row exists for the second inviter yet: business uniqueness,
                # not a request-key collision, blocks the identical target/body.
                self.response(case, case.invite('alpha', key=key, actor='other'),
                              409, 'already_invited')
                self.assertEqual(case.snapshot(), before)
                self.records(case, [('alpha', 'admin', key, admin)])
                self.assertEqual(case.audit(), [
                    ('admin', 'alpha', 'invitation.create', admin['id'], 1)])

                # The actor-axis success needs a different target to satisfy the
                # workspace/target business rule; this is not an identical body.
                other = self.invitation(case,
                    case.invite('alpha', key=key, actor='other', target='viewer'),
                    'alpha', target='viewer')
                self.assertNotEqual(admin['id'], other['id'])
                expected = [('alpha', 'admin', key, admin), ('alpha', 'other', key, other)]
                self.records(case, expected)
                self.assertEqual(self.target_memberships(case), [])
                audits = [('admin', 'alpha', 'invitation.create', admin['id'], 1),
                          ('other', 'alpha', 'invitation.create', other['id'], 1)]
                self.assertEqual(case.audit(), audits)
                before = case.snapshot()
                for actor, target, original in (('admin', 'outsider', admin),
                                                 ('other', 'viewer', other)):
                    with self.subTest(actor=actor):
                        self.assertEqual(self.response(case,
                            case.invite('alpha', key=key, actor=actor, target=target)), original)
                        self.assertEqual(case.snapshot(), before)
                # Now the second inviter owns a row: changing its target conflicts
                # in that exact namespace before the actionable-target check.
                self.response(case, case.invite('alpha', key=key, actor='other'),
                              409, 'idempotency_conflict')
                self.assertEqual(case.snapshot(), before)
                self.records(case, expected)
                self.assertEqual(case.audit(), audits)

    def test_same_key_and_body_are_workspace_scoped_including_shared_space(self):
        for boundary in f.BOUNDARIES:
            with self.subTest(boundary=boundary), f.RetryNamespaceFixture(boundary) as case:
                key = str(uuid4())
                items = {workspace: self.invitation(case, case.invite(workspace, key=key), workspace)
                         for workspace in ('alpha', 'beta', 'commons')}
                self.assertEqual(len({item['id'] for item in items.values()}), 3)
                expected = [(workspace, 'admin', key, item) for workspace, item in items.items()]
                self.records(case, expected)
                self.assertEqual(self.target_memberships(case), [])
                audits = [('admin', workspace, 'invitation.create', item['id'], 1)
                          for workspace, item in items.items()]
                self.assertEqual(case.audit(), audits)
                before = case.snapshot()
                for workspace, original in items.items():
                    with self.subTest(workspace=workspace):
                        self.assertEqual(self.response(case, case.invite(workspace, key=key)), original)
                        self.assertEqual(case.snapshot(), before)
                self.records(case, expected)
                self.assertEqual(self.target_memberships(case), [])
                self.assertEqual(case.audit(), audits)

    def test_body_conflict_and_current_admin_denial_do_not_poison_another_workspace(self):
        for boundary in f.BOUNDARIES:
            for member_state, status, error in (('revoked', 404, 'not_found'),
                                                  ('active', 403, 'forbidden')):
                with self.subTest(boundary=boundary, member_state=member_state), \
                        f.RetryNamespaceFixture(boundary) as case:
                    key = str(uuid4())
                    items = {workspace: self.invitation(case, case.invite(workspace, key=key), workspace)
                             for workspace in ('alpha', 'beta')}
                    self.assertNotEqual(items['alpha']['id'], items['beta']['id'])
                    expected = [(workspace, 'admin', key, item) for workspace, item in items.items()]
                    audits = [('admin', workspace, 'invitation.create', item['id'], 1)
                              for workspace, item in items.items()]
                    self.records(case, expected)
                    self.assertEqual(case.audit(), audits)
                    before = case.snapshot()
                    self.response(case, case.invite('alpha', key=key, role='developer'),
                                  409, 'idempotency_conflict')
                    self.assertEqual(case.snapshot(), before)

                    # Actual membership PATCH, never identity mutation or SQL DML.
                    member = self.response(case, case.member('alpha', 'admin',
                                           actor='other', role='viewer', state=member_state))
                    self.assertEqual(member, {'workspace_id': 'alpha', 'principal_id': 'admin',
                                              'role': 'viewer', 'state': member_state, 'revision': 2})
                    audits.append(('other', 'alpha', 'membership.update', 'admin', 2))
                    self.assertEqual(case.audit(), audits)
                    # Legitimate revocation/demotion changed policy and audit; the
                    # invariant starts after it, not before the authorized PATCH.
                    before = case.snapshot()
                    self.response(case, case.invite('alpha', key=key), status, error)
                    self.assertEqual(case.snapshot(), before)
                    self.assertEqual(self.response(case, case.invite('beta', key=key)), items['beta'])
                    self.assertEqual(case.snapshot(), before)
                    self.records(case, expected)
                    self.assertEqual(self.target_memberships(case), [])
                    self.assertEqual(case.audit(), audits)

    def test_historical_states_replay_only_their_namespace_without_regranting(self):
        for boundary in f.BOUNDARIES:
            for history in ('accepted', 'invalidated'):
                with self.subTest(boundary=boundary, history=history), \
                        f.RetryNamespaceFixture(boundary) as case:
                    key = str(uuid4())
                    items = {workspace: self.invitation(case, case.invite(workspace, key=key), workspace)
                             for workspace in ('alpha', 'beta')}
                    self.assertNotEqual(items['alpha']['id'], items['beta']['id'])
                    self.assertEqual(self.target_memberships(case), [])
                    audits = [('admin', workspace, 'invitation.create', item['id'], 1)
                              for workspace, item in items.items()]
                    self.assertEqual(case.audit(), audits)
                    if history == 'accepted':
                        historical = self.response(case,
                            case.call('/api/invitations/' + items['alpha']['id'], actor='outsider',
                                      method='PATCH', body={'state': 'accepted'}, revision=1))
                        self.assertEqual(historical, items['alpha'] | {'state': 'accepted', 'revision': 2})
                        audits.extend([('outsider', 'alpha', 'membership.join', 'outsider', 1),
                                       ('outsider', 'alpha', 'invitation.accepted', historical['id'], 2)])
                        self.assertEqual(case.audit(), audits)
                        self.assertEqual(self.target_memberships(case),
                                         [('alpha', 'outsider', 'viewer', 'active', 1)])
                        member = self.response(case, case.member('alpha', 'outsider', actor='admin'))
                        self.assertEqual(member, {'workspace_id': 'alpha', 'principal_id': 'outsider',
                                                  'role': 'viewer', 'state': 'revoked', 'revision': 2})
                        audits.append(('admin', 'alpha', 'membership.update', 'outsider', 2))
                        stored = historical
                        memberships = [('alpha', 'outsider', 'viewer', 'revoked', 2)]
                    else:
                        # Restore current permission, not the captured revision.
                        # Only alpha changes; beta's invitation remains actionable.
                        for role, revision in (('viewer', 1), ('admin', 2)):
                            member = self.response(case, case.member('alpha', 'admin', actor='other',
                                role=role, state='active', revision=revision))
                            self.assertEqual(member, {'workspace_id': 'alpha', 'principal_id': 'admin',
                                                      'role': role, 'state': 'active', 'revision': revision + 1})
                            audits.append(('other', 'alpha', 'membership.update', 'admin', revision + 1))
                            self.assertEqual(case.audit(), audits)
                        historical = items['alpha'] | {'state': 'invalidated'}
                        # Invalidated is a derived view; the stored row stays pending.
                        stored = items['alpha']
                        memberships = []
                    self.assertEqual(case.audit(), audits)
                    self.assertEqual(self.target_memberships(case), memberships)
                    before = case.snapshot()
                    # Both historical states are valid metadata, not admin
                    # provenance conflicts. POST replay never grants membership.
                    self.assertEqual(self.response(case, case.invite('alpha', key=key)), historical)
                    self.assertEqual(case.snapshot(), before)
                    self.assertEqual(self.response(case, case.invite('beta', key=key)), items['beta'])
                    self.assertEqual(case.snapshot(), before)
                    if history == 'invalidated':
                        self.response(case,
                            case.call('/api/invitations/' + items['alpha']['id'], actor='outsider',
                                      method='PATCH', body={'state': 'accepted'}, revision=1),
                            409, 'invitation_unavailable')
                        self.assertEqual(case.snapshot(), before)
                    self.records(case, [('alpha', 'admin', key, stored),
                                        ('beta', 'admin', key, items['beta'])])
                    self.assertEqual(self.target_memberships(case), memberships)
                    self.assertEqual(case.audit(), audits)


if __name__ == '__main__':
    unittest.main()
