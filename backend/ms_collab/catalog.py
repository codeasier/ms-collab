"""Explicit synthetic data and module descriptors, never loaded from production."""
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class Workspace:
    id: str
    name: str
    kind: str
    modules: tuple[str, ...]


@dataclass(frozen=True)
class Module:
    id: str
    name: str
    capability: str
    description: str


WORKSPACES = MappingProxyType({
    "product-alpha": Workspace("product-alpha", "Product Alpha", "private", ("overview",)),
    "product-beta": Workspace("product-beta", "Product Beta", "private", ("overview",)),
    "commons": Workspace("commons", "Shared collaboration", "public", ("overview",)),
})
MODULES = MappingProxyType({
    "overview": Module("overview", "Overview", "overview.read", "Synthetic workspace overview"),
})
# A fixed, synthetic server-side principal. There is no login or user selection.
# The public workspace still requires explicit membership. Never use this as auth.
DEMO_USER = "demo-developer"
MEMBERSHIPS = MappingProxyType({
    "product-alpha": frozenset({"overview.read"}),
    "commons": frozenset({"overview.read"}),
})
OVERVIEWS = MappingProxyType({
    "product-alpha": {"summary": "Example product-team workspace", "repositories": 2, "open_items": 5},
    "product-beta": {"summary": "Example workspace outside this demo identity", "repositories": 1, "open_items": 3},
    "commons": {"summary": "Explicitly shared synthetic information only", "repositories": 0, "open_items": 1},
})


def visible_workspaces():
    return [{"id": w.id, "name": w.name, "kind": w.kind}
            for w in WORKSPACES.values() if w.id in MEMBERSHIPS]


def visible_modules(workspace_id):
    workspace = WORKSPACES[workspace_id]
    capabilities = MEMBERSHIPS.get(workspace_id, frozenset())
    return [{"id": m.id, "name": m.name, "capability": m.capability,
             "description": m.description}
            for module_id in workspace.modules
            if (m := MODULES[module_id]).capability in capabilities]
