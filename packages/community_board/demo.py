"""Entirely synthetic fixtures, with no provider calls or persisted data."""
from .model import BoardItem, BoardProjection, Scope, Snapshot


def demo_board() -> BoardProjection:
    stamp = "2026-01-01T00:00:00Z"
    return BoardProjection([
        Snapshot(Scope("product-alpha", "demo", "example/compiler"), stamp, (
            BoardItem("issue", 1, "Improve compiler diagnostics", "open", stamp, ("usability",)),
            BoardItem("pull", 2, "Add synthetic parser fixtures", "merged", stamp, ("tests",)),
        )),
        Snapshot(Scope("product-beta", "demo", "example/profiler"), stamp, (
            BoardItem("issue", 1, "Show sample trace summary", "open", stamp, ("visualization",)),
        )),
        Snapshot(Scope("commons", "demo", "example/team-guide"), stamp, (
            BoardItem("issue", 1, "Document contribution workflow", "open", stamp, ("documentation",)),
        )),
    ])
