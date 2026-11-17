"""Operator lifecycle and workspace-admin module control. No default providers."""
from dataclasses import asdict, dataclass

from packages.workspace_api import WorkspaceApplication
from packages.workspace_api.application import _SEGMENT, _invalid
from packages.workspaces import PolicyError
from .requests import request_key, remembered, remember


@dataclass(frozen=True)
class ModuleTemplate:
    """Platform-approved configuration; never constructed from request claims."""
    key: str
    capabilities: tuple[str, ...] = ("read",)

    def __post_init__(self):
        if (type(self.key) is not str or not _SEGMENT.fullmatch(self.key)
                or type(self.capabilities) is not tuple or not self.capabilities
                or any(type(cap) is not str or cap not in {"read", "write"} for cap in self.capabilities)
                or len(set(self.capabilities)) != len(self.capabilities)):
            raise ValueError("invalid approved module template")


class WorkspaceAdministrationApplication(WorkspaceApplication):
    """Explicit composition; inherited response handling never exposes internals.

    Existing WorkspaceApplication routes are delegated unchanged. The fixed
    catalog only permits installing a disabled local module. It does not grant
    connector authority, execute jobs, provision secrets, or install software.
    """
    def __init__(self, *, api, module_catalog=()):
        if type(api) is not WorkspaceApplication:
            raise ValueError("explicit configured workspace API required")
        catalog = tuple(module_catalog)
        if any(type(item) is not ModuleTemplate for item in catalog):
            raise ValueError("approved module templates required")
        if len({item.key for item in catalog}) != len(catalog):
            raise ValueError("duplicate module template")
        self._api, self._catalog = api, {item.key: item for item in catalog}

    def _dispatch(self, environ):
        path = environ.get("PATH_INFO", "")
        if type(path) is not str or len(path) > 1000:
            raise _invalid()
        parts = path.split("/")
        is_control = parts[:3] == ["", "api", "control"]
        is_module = (parts[:3] == ["", "api", "workspaces"]
                     and len(parts) in {5, 6} and parts[4] in {"modules", "module-catalog"})
        if not is_control and not is_module:
            return self._api._dispatch(environ)
        assertion = self._api._verify_request(environ)
        if environ.get("QUERY_STRING", "") or any(not _SEGMENT.fullmatch(p) for p in parts[3:]):
            raise _invalid()
        verb = environ.get("REQUEST_METHOD")
        command, args, body, write = None, (), None, False
        if is_control:
            if parts == ["", "api", "control", "capabilities"] and verb == "GET":
                command = "capabilities"
            elif parts == ["", "api", "control", "workspaces"] and verb == "GET":
                command = "control_list"
            elif len(parts) == 5 and parts[3] == "initial-admin-selections" and verb == "GET":
                command, args = "initial_admin_selection", (parts[4],)
            elif parts == ["", "api", "control", "workspaces"] and verb == "POST":
                command, write = "create", True
                body = self._body(environ, {"name", "kind", "initial_admin"})
                args = (request_key(environ),)
            elif len(parts) == 5 and parts[3] == "workspaces" and verb == "PATCH":
                command, write = "lifecycle", True
                body = self._body(environ, {"state"})
                args = (parts[4], self._revision(environ))
        elif len(parts) == 5 and parts[4] == "module-catalog" and verb == "GET":
            command, args = "catalog", (parts[3],)
        elif parts[4] == "modules":
            if len(parts) == 5 and verb == "GET":
                command, args = "modules", (parts[3],)
            elif len(parts) == 5 and verb == "POST":
                command, args, write = "install", (parts[3], request_key(environ)), True
                body = self._body(environ, {"key"})
            elif len(parts) == 6 and verb == "PATCH":
                command, args, write = "module_state", (parts[3], parts[5], self._revision(environ)), True
                body = self._body(environ, {"state"})
        if command is None:
            raise PolicyError(404, "not_found")

        def operation(policy, actor, connection):
            if command == "capabilities":
                policy._principal(actor)
                return {"manage_workspaces": policy._operator_current(actor)}
            if command == "control_list":
                return [asdict(w) for w in policy.list_control_workspaces(actor)]
            if command == "initial_admin_selection":
                return policy.initial_admin_selection(actor, args[0])
            if command == "lifecycle":
                return asdict(policy.set_workspace_state(actor, args[0],
                              state=body["state"], expected_revision=args[1]))
            if command == "create":
                policy._operator(actor)
                if not _SEGMENT.fullmatch(body["initial_admin"]):
                    raise _invalid()
                result, identity, digest = remembered(policy, connection, actor, "workspace.create", "platform", args[0], body)
                if result is not None:
                    return asdict(policy._workspaces[result])
                # Exact old-key replay is resolved above, without interpreting
                # a new selection header or changing its durable body digest.
                # Only a fresh mutation requires the retained selection, checked
                # inside this same writer transaction by the core service.
                selected = environ.get("HTTP_IF_INITIAL_ADMIN_MATCH")
                if "HTTP_IF_INITIAL_ADMIN_MATCH" in environ and type(selected) is not str:
                    raise PolicyError(400, "invalid_initial_admin_selection")
                workspace = policy.create_workspace(actor, **body,
                    expected_initial_admin_selection=selected)
                remember(policy, connection, identity, digest, workspace.id, body)
                return asdict(workspace)
            workspace = args[0]
            if command == "modules":
                return [asdict(m) for m in policy.list_modules(actor, workspace)]
            policy._admin(actor, workspace)
            if command == "catalog":
                return [asdict(m) for m in sorted(self._catalog.values(), key=lambda m: m.key)]
            if command == "install":
                result, identity, digest = remembered(policy, connection, actor, "module.install", workspace, args[1], body)
                if result is not None:
                    return asdict(policy.get_module(actor, workspace, result))
                template = self._catalog.get(body["key"])
                if template is None:
                    raise PolicyError(400, "module_not_approved")
                module = policy.install_module(actor, workspace, template.key, capabilities=template.capabilities)
                remember(policy, connection, identity, digest, module.key, body)
                return asdict(module)
            if command == "module_state":
                # Current catalog authority is required to re-enable. Removal or
                # capability changes never silently expand an installed module.
                if body["state"] == "enabled":
                    module = policy.get_module(actor, workspace, args[1])
                    template = self._catalog.get(args[1])
                    if template is None or set(template.capabilities) != set(module.capabilities):
                        raise PolicyError(409, "module_config_not_approved")
                return asdict(policy.set_module_state(actor, workspace, args[1],
                              state=body["state"], expected_revision=args[2]))
            raise PolicyError(404, "not_found")
        return self._api._authenticated_transaction(assertion, operation, write=write, with_connection=True)
