# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Pure synthetic grant capture/validation; no production identity mutations."""
from copy import deepcopy
from dataclasses import replace
import unittest

from packages.workspaces.authority import capture_authority, validate_authority, authority_current, principal_pin
from packages.workspaces.tests.test_service import fixture


class GrantFenceTests(unittest.TestCase):
    def setUp(self):
        self.policy = fixture()
        self.grant = capture_authority(self.policy, ('viewer','alpha_admin'))

    def test_capture_pins_exact_current_id_generation_and_realm(self):
        self.assertEqual(set(self.grant), {'version','realm','principals'})
        self.assertEqual(set(self.grant['principals']), {'viewer','alpha_admin'})
        self.assertEqual(self.grant['realm'], self.policy._realm)
        self.assertTrue(authority_current(self.policy,'viewer',self.grant))
        self.assertFalse(authority_current(self.policy,'multi',self.grant))
        self.assertFalse(authority_current(self.policy,'missing',self.grant))
        self.assertEqual(principal_pin(self.policy,'viewer')[0], self.policy._realm)

    def test_exact_recipient_set_missing_unknown_or_legacy_fields_deny(self):
        for changed in ({},None,{'principals':{}},dict(self.grant,version=True),dict(self.grant,realm='invalid'),
                        dict(self.grant,principals={}),dict(self.grant,extra=True)):
            with self.subTest(changed=changed),self.assertRaises(ValueError):
                validate_authority(changed,('viewer','alpha_admin'))
        for key in ('issuer','subject','generation'):
            changed=deepcopy(self.grant);changed['principals']['viewer'].pop(key)
            with self.assertRaises(ValueError):validate_authority(changed,('viewer','alpha_admin'))
            self.assertFalse(authority_current(self.policy,'viewer',changed))

    def test_generation_requires_bounded_exact_integer(self):
        for invalid in (True,0,-1,1.0,'1',9007199254740992):
            changed=deepcopy(self.grant);changed['principals']['viewer']['generation']=invalid
            with self.subTest(invalid=invalid),self.assertRaises(ValueError):
                validate_authority(changed,('viewer','alpha_admin'))

    def test_simulated_identity_aba_and_reactivation_do_not_restore_old_grant(self):
        original=self.policy._principals['viewer']
        self.policy._principals['viewer']=replace(original,subject='replacement',generation=2)
        self.assertFalse(authority_current(self.policy,'viewer',self.grant))
        self.policy._principals['viewer']=replace(original,generation=3)
        self.assertFalse(authority_current(self.policy,'viewer',self.grant))
        self.policy._principals['viewer']=replace(original,active=False,generation=4)
        self.assertFalse(authority_current(self.policy,'viewer',self.grant))
        self.policy._principals['viewer']=replace(original,generation=5)
        self.assertFalse(authority_current(self.policy,'viewer',self.grant))
        # Only explicit trusted recapture creates a different grant.
        fresh=capture_authority(self.policy,('viewer','alpha_admin'))
        self.assertNotEqual(fresh,self.grant)
        self.assertTrue(authority_current(self.policy,'viewer',fresh))

    def test_foreign_realm_and_changed_id_binding_are_not_grants(self):
        other=fixture()
        self.assertFalse(authority_current(other,'viewer',self.grant))
        for field in ('issuer','subject'):
            changed=deepcopy(self.grant);changed['principals']['viewer'][field]='replacement'
            self.assertFalse(authority_current(self.policy,'viewer',changed))

    def test_empty_recipient_set_never_authorizes_and_unknown_capture_rejects(self):
        empty=capture_authority(self.policy,())
        validate_authority(empty,())
        self.assertFalse(authority_current(self.policy,'viewer',empty))
        with self.assertRaises(ValueError):capture_authority(self.policy,('missing',))


if __name__=='__main__':unittest.main()
