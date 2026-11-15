# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Admission-to-inspect ABA and cleanup invariants with synthetic session I/O."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import unittest
from unittest.mock import patch

import test_session as fixture_cases
from packages.artifact_refresh import RefreshCleanupError
from packages.artifact_sync import regrant_index,records
from packages.artifacts import Scope


class RefreshAuthorityTests(unittest.TestCase):
    setUp=fixture_cases.RefreshTests.setUp
    call=fixture_cases.RefreshTests.call
    sql=fixture_cases.RefreshTests.sql
    state=fixture_cases.RefreshTests.state
    leases=fixture_cases.RefreshTests.leases

    def member_aba(self):
        self.policy.set_membership('second','alpha','admin',role='developer',state='active',expected_revision=1)
        self.policy.set_membership('second','alpha','admin',role='admin',state='active',expected_revision=2)

    def test_admission_to_inspect_barrier_pins_original_membership(self):
        entered=Event();release=Event();self.addCleanup(release.set)
        original=self.controller.service._refresh_verified
        def after_admission(*args,**kwargs):
            entered.set()
            if not release.wait(10):raise RuntimeError('synthetic timeout')
            return original(*args,**kwargs)
        before=self.state()
        with patch.object(self.controller.service,'_refresh_verified',side_effect=after_admission),ThreadPoolExecutor(max_workers=1) as pool:
            pending=pool.submit(self.call);self.assertTrue(entered.wait(10))
            self.assertEqual(self.leases(),[('admin','active',None)])
            self.member_aba();release.set()
            self.assertEqual(pending.result(timeout=10)['status'],412)
        self.assertEqual(self.pages.loads,[]);self.assertEqual(self.pages.calls,[])
        self.assertEqual(self.state(),before);self.assertEqual(self.leases(),[('admin','failed',None)])
        self.assertFalse(self.sql('SELECT * FROM artifact_index_events_v1'))

    def test_admission_to_inspect_module_aba_is_not_a_new_pin_start(self):
        original=self.controller.service._refresh_verified
        def after_admission(*args,**kwargs):
            self.policy.set_module_state('admin','alpha','artifacts',state='disabling',expected_revision=1)
            self.policy.set_module_state('admin','alpha','artifacts',state='disabled',expected_revision=2)
            self.policy.set_module_state('admin','alpha','artifacts',state='enabled',expected_revision=3)
            return original(*args,**kwargs)
        before=self.state()
        with patch.object(self.controller.service,'_refresh_verified',side_effect=after_admission):
            self.assertEqual(self.call()['status'],412)
        self.assertEqual(self.state(),before);self.assertEqual(self.pages.loads,[])
        self.assertEqual(self.leases(),[('admin','failed',None)])

    def test_revision_max_has_no_lease_or_provider_side_effects(self):
        self.sql('UPDATE artifact_indexes_v1 SET revision=?',(records.MAX_REVISION,))
        before=self.state()
        self.assertEqual(self.call(revision=records.MAX_REVISION)['status'],409)
        self.assertEqual(self.state(),before);self.assertFalse(self.leases())
        self.assertFalse(self.pages.loads);self.assertFalse(self.pages.calls)

    def test_uncertain_cleanup_plus_aba_and_regrant_never_recycle_active_lease(self):
        def fail():
            self.member_aba();raise RefreshCleanupError()
        self.pages.page_hook=fail
        self.assertIn(self.call()['status'],(502,503))
        self.assertEqual(self.leases(),[('admin','active',None)])
        scope=Scope('alpha','source','bucket')
        config=self.policy._transaction(lambda p,c:records.load(c,scope).policy,write=False,with_connection=True)
        regrant_index(self.policy,config,actor_id='admin',expected_revision=1,expected_grant_revision=1)
        self.assertEqual(self.leases(),[('admin','active',None)])
        self.assertEqual(self.call(revision=2)['status'],429)
        self.assertEqual(len(self.pages.calls),1);self.assertIsNone(self.state()[1])


if __name__=='__main__':unittest.main()
