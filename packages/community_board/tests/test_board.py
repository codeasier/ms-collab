from dataclasses import replace
import unittest

from packages.community_board import (
    BoardItem, BoardProjection, Scope, Snapshot, UnknownWorkspace, compare_snapshots,
)
from packages.community_board.demo import demo_board


T0 = "2026-01-01T00:00:00Z"
T1 = "2026-01-02T00:00:00Z"
SCOPE = Scope("product-alpha", "demo", "example/compiler")


def record(number=1, **kwargs):
    defaults = dict(kind="issue", number=number, title="Synthetic task", state="open", updated_at=T0)
    defaults.update(kwargs)
    return BoardItem(**defaults)


def snapshot(*items, observed_at=T1, scope=SCOPE):
    return Snapshot(scope, observed_at, items)


class BoardProjectionTest(unittest.TestCase):
    def test_workspace_is_explicit_and_never_unioned(self):
        board = demo_board()
        alpha = board.list_items("product-alpha")
        self.assertEqual(alpha["total"], 2)
        self.assertEqual({i["workspace_id"] for i in alpha["items"]}, {"product-alpha"})
        self.assertEqual(board.list_items("commons")["total"], 1)
        self.assertEqual({s["workspace_id"] for s in alpha["sources"]}, {"product-alpha"})

    def test_unknown_workspace_fails_closed(self):
        with self.assertRaises(UnknownWorkspace):
            demo_board().list_items("missing")
        for scope in ("", "*", "../product-alpha", None):
            with self.subTest(scope=scope), self.assertRaises(ValueError):
                demo_board().list_items(scope)

    def test_same_number_and_kind_across_scopes_remain_distinct(self):
        scopes = [
            SCOPE, Scope("product-alpha", "other", "example/compiler"),
            Scope("product-alpha", "demo", "example/profiler"),
            Scope("product-beta", "demo", "example/compiler"),
        ]
        board = BoardProjection([snapshot(record(), scope=s) for s in scopes])
        page = board.list_items("product-alpha")
        self.assertEqual(page["total"], 3)
        self.assertEqual(len({(i["connection_id"], i["repository"], i["kind"], i["number"])
                              for i in page["items"]}), 3)

    def test_issue_and_pull_number_spaces_are_distinct(self):
        page = BoardProjection([snapshot(record(), record(kind="pull"))]).list_items("product-alpha")
        self.assertEqual(page["total"], 2)
        self.assertEqual(page["counts"]["issue"], 1)
        self.assertEqual(page["counts"]["pull"], 1)

    def test_filter_search_counts_and_pagination(self):
        board = BoardProjection([snapshot(
            record(1, labels=("BUG",)), record(3, title="Compiler change"),
            record(2, kind="pull", state="merged"),
        )])
        self.assertEqual(board.list_items("product-alpha", query="bug")["total"], 1)
        self.assertEqual(board.list_items("product-alpha", query="compiler")["total"], 1)
        page = board.list_items("product-alpha", kind="issue", state="open", limit=1, offset=1)
        self.assertEqual(page["total"], 2)
        self.assertEqual(page["counts"]["open"], 2)
        self.assertEqual(page["items"][0]["number"], 1)
        self.assertEqual(board.list_items("product-alpha", offset=50)["items"], [])

    def test_invalid_filters_are_not_silently_ignored(self):
        board = demo_board()
        invalid = [dict(kind="all"), dict(state="unknown"), dict(query=4), dict(query="x"*201),
                   dict(limit=0), dict(limit=101), dict(limit=True), dict(offset=-1), dict(offset=True)]
        for kwargs in invalid:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                board.list_items("product-alpha", **kwargs)

    def test_no_aliasing_from_input_or_output(self):
        labels = ["synthetic"]
        item = record(labels=labels)
        records = [item]
        snapshots = [Snapshot(SCOPE, T1, records)]
        board = BoardProjection(snapshots)
        labels.append("new")
        records.clear()
        snapshots.clear()
        page = board.list_items("product-alpha")
        page["items"][0]["labels"].append("mutated")
        page["items"][0]["title"] = "mutated"
        page["sources"][0]["repository"] = "mutated"
        again = board.list_items("product-alpha")
        self.assertEqual(again["items"][0]["labels"], ["synthetic"])
        self.assertEqual(again["items"][0]["title"], "Synthetic task")
        self.assertEqual(again["sources"][0]["repository"], "example/compiler")

    def test_duplicate_scopes_and_items_rejected(self):
        with self.assertRaises(ValueError):
            snapshot(record(), record())
        with self.assertRaises(ValueError):
            BoardProjection([snapshot(record()), snapshot(record(2))])

    def test_empty_snapshot_still_registers_workspace(self):
        result = BoardProjection([snapshot()]).list_items("product-alpha")
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["coverage"], "observed_subset")
        self.assertEqual(len(result["sources"]), 1)

    def test_stable_sort_ignores_input_order(self):
        first = snapshot(record(1), record(4), record(2, kind="pull"))
        second = replace(first, items=tuple(reversed(first.items)))
        self.assertEqual(BoardProjection([first]).list_items("product-alpha"),
                         BoardProjection([second]).list_items("product-alpha"))

    def test_raw_fields_are_not_accepted_or_fabricated(self):
        with self.assertRaises(TypeError):
            record(token="not-a-credential")
        item = demo_board().list_items("commons")["items"][0]
        self.assertNotIn("url", item)
        self.assertNotIn("user", item)
        self.assertNotIn("body", item)


class ValidationTest(unittest.TestCase):
    def test_invalid_item_contract(self):
        cases = [dict(kind="task"), dict(number=True), dict(number=0), dict(number="1"),
                 dict(title=""), dict(title="x"*1001), dict(state="deleted"), dict(state="merged"),
                 dict(labels="bug"), dict(labels=("",)), dict(labels=(1,)),
                 dict(updated_at="not-a-time"), dict(updated_at="2026-01-01T00:00:00")]
        for kwargs in cases:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                record(**kwargs)

    def test_invalid_scope_contract(self):
        for repository in ("example", "example/repo/nested", "../repo", "example/", None):
            with self.subTest(repository=repository), self.assertRaises(ValueError):
                Scope("product-alpha", "demo", repository)

    def test_snapshot_requires_aware_observation(self):
        with self.assertRaises(ValueError):
            snapshot(record(), observed_at="2026-01-01")

    def test_future_item_is_rejected(self):
        with self.assertRaises(ValueError):
            snapshot(record(updated_at=T1), observed_at=T0)

    def test_timezone_equivalence_supported(self):
        snap = snapshot(record(updated_at="2026-01-01T08:00:00+08:00"), observed_at=T0)
        self.assertEqual(len(snap.items), 1)


class DeltaTest(unittest.TestCase):
    def test_first_snapshot_is_baseline_not_new_items(self):
        result = compare_snapshots(None, snapshot(record()))
        self.assertFalse(result["has_previous"])
        self.assertEqual(result["new"], [])

    def test_explicit_lifecycle_and_content_changes(self):
        previous = snapshot(record(1), record(2, kind="pull"), record(3, state="closed"), record(4))
        current = snapshot(record(1, state="closed"), record(2, kind="pull", state="merged"),
                           record(3), record(4, title="New title"), record(5))
        delta = compare_snapshots(previous, current)
        for key, number in [("closed", 1), ("merged", 2), ("reopened", 3), ("updated", 4), ("new", 5)]:
            self.assertEqual([i["number"] for i in delta[key]], [number])

    def test_absence_is_not_closure_or_deletion(self):
        delta = compare_snapshots(snapshot(record(), record(2, kind="pull")), snapshot())
        self.assertEqual(delta["closed"], [])
        self.assertEqual(delta["merged"], [])
        self.assertEqual(delta["unavailable"], [{"kind": "issue", "number": 1}, {"kind": "pull", "number": 2}])

    def test_cross_workspace_repository_or_connection_refused(self):
        for changed in (Scope("product-beta", "demo", "example/compiler"),
                        Scope("product-alpha", "other", "example/compiler"),
                        Scope("product-alpha", "demo", "example/profiler")):
            with self.subTest(scope=changed), self.assertRaises(ValueError):
                compare_snapshots(snapshot(record()), snapshot(record(), scope=changed))

    def test_stale_snapshot_refused(self):
        with self.assertRaises(ValueError):
            compare_snapshots(snapshot(record(), observed_at=T1), snapshot(record(), observed_at=T0))

    def test_stale_item_refused(self):
        with self.assertRaises(ValueError):
            compare_snapshots(snapshot(record(updated_at=T1)), snapshot(record(updated_at=T0)))

    def test_unchanged_no_events_and_no_input_mutation(self):
        snap = snapshot(record())
        delta = compare_snapshots(snap, snap)
        self.assertTrue(delta["has_previous"])
        self.assertTrue(all(not delta[key] for key in ("new", "closed", "merged", "updated", "reopened", "unavailable")))
        delta["scope"]["workspace_id"] = "modified"
        self.assertEqual(snap.scope.workspace_id, "product-alpha")

    def test_mixed_offsets_compare_without_naive_datetime(self):
        previous = snapshot(record(updated_at="2026-01-01T08:00:00+08:00"), observed_at=T0)
        current = snapshot(record(updated_at="2026-01-01T01:00:00Z"), observed_at=T1)
        self.assertEqual(len(compare_snapshots(previous, current)["updated"]), 1)

    def test_item_payload_is_detached_from_delta(self):
        current = snapshot(record(labels=("synthetic",)))
        result = compare_snapshots(snapshot(), current)
        result["new"][0]["labels"].append("changed")
        self.assertEqual(current.items[0].labels, ("synthetic",))


if __name__ == "__main__":
    unittest.main()
