"""Provider-neutral, read-only community-board projections.

This package is not an authentication boundary. Authorize the requested workspace
in the calling application before using these in-memory projection helpers.
"""

from .model import BoardItem, BoardProjection, Scope, Snapshot, UnknownWorkspace
from .delta import compare_snapshots

__all__ = [
    "BoardItem", "BoardProjection", "Scope", "Snapshot", "UnknownWorkspace",
    "compare_snapshots",
]
