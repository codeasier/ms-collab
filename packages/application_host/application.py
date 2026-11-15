"""A fixed route registry sharing the same session/policy transaction authority."""
from dataclasses import dataclass
from datetime import datetime, timezone

from packages.assistant_history import AssistantHistoryApplication
from packages.gui_smoke import GuiSmokeApplication, SmokeConnection
from packages.artifact_sync import IndexedArtifactApplication, ArtifactSynchronizer, ProviderError
from packages.artifact_admin import ArtifactAdministrationAPI
from packages.artifact_download import ArtifactDownloader, ArtifactDownloadConfiguration, ArtifactSessionDownloadAPI
from packages.community_api import CommunityApplication
from packages.pipeline_api import PipelineApplication
from packages.research_assets import AssetApplication
from packages.responsibility_api import ResponsibilityApplication
from packages.session_api import SessionPolicyStore, SessionWorkspaceApplication
from packages.workspace_api import WorkspaceApplication
from packages.workspace_admin_api import ModuleTemplate, WorkspaceAdministrationApplication
from packages.workspace_invitations import WorkspaceInvitationApplication
from packages.workspaces import PolicyError

_MODULES = frozenset({"community", "pipelines", "artifacts", "research-assets", "responsibility", "assistant", "gui-smoke"})


@dataclass(frozen=True)
class HostConfig:
    """Trusted, immutable configuration. It grants no user or workspace rights."""
    origin: str
    modules: tuple[str, ...] = ()
    module_catalog: tuple[ModuleTemplate, ...] = ()
    gui_smoke_connections: tuple[SmokeConnection, ...] = ()
    artifact_administration: bool = False
    artifact_downloads: bool = False

    def __post_init__(self):
        if (type(self.modules) is not tuple or any(type(k) is not str or k not in _MODULES for k in self.modules)
                or len(set(self.modules)) != len(self.modules)
                or type(self.module_catalog) is not tuple
                or any(type(t) is not ModuleTemplate or t.key not in {k.replace("-", "_") if k in {"research-assets", "gui-smoke"} else k for k in self.modules} for t in self.module_catalog)
                or len({t.key for t in self.module_catalog}) != len(self.module_catalog)):
            raise ValueError("explicit supported modules and catalog required")
        if (type(self.gui_smoke_connections) is not tuple or len(self.gui_smoke_connections) > 100
                or any(type(c) is not SmokeConnection for c in self.gui_smoke_connections)
                or len({c.scope for c in self.gui_smoke_connections}) != len(self.gui_smoke_connections)
                or (self.gui_smoke_connections and "gui-smoke" not in self.modules)):
            raise ValueError("explicit GUI smoke configuration requires its hosted module")
        if type(self.artifact_administration) is not bool or (self.artifact_administration and "artifacts" not in self.modules):
            raise ValueError("artifact administration requires its explicit hosted module")
        if type(self.artifact_downloads) is not bool or (self.artifact_downloads and "artifacts" not in self.modules):
            raise ValueError("artifact downloads require the explicit hosted module")
        if "responsibility" in self.modules and "community" not in self.modules:
            raise ValueError("responsibility requires the configured community boundary")


class ApplicationHost:
    """Configured WSGI factory, not a standalone/demo/production server.

    No implicit DB creation, directory enrollment, fixtures, external acquisition,
    environment imports, route plugins, default verifier or listener. Missing real
    bound-code evidence is not solved by enabling a flag or supplying actor IDs.
    """
    def __init__(self, *, config: HostConfig, sessions: SessionPolicyStore,
                 exchange=None, authorization_endpoint=None, authorization_scopes=(),
                 clock=lambda: datetime.now(timezone.utc), artifact_downloads=None):
        if type(config) is not HostConfig or type(sessions) is not SessionPolicyStore or not callable(clock):
            raise ValueError("explicit host configuration, initialized stores and clock required")
        if (config.artifact_downloads and type(artifact_downloads) is not ArtifactDownloadConfiguration
                or not config.artifact_downloads and artifact_downloads is not None):
            raise ValueError('explicit download switch and trusted configuration required together')
        self._config = config
        self._boundary = SessionWorkspaceApplication(sessions=sessions, origin=config.origin,
            exchange=exchange, authorization_endpoint=authorization_endpoint,
            authorization_scopes=authorization_scopes)
        authority = WorkspaceApplication(session_boundary=self._boundary)
        administration = WorkspaceAdministrationApplication(api=authority, module_catalog=config.module_catalog)
        self._workspaces = WorkspaceInvitationApplication(api=administration, clock=clock)
        constructors = {"community": CommunityApplication, "pipelines": PipelineApplication,
                        "artifacts": IndexedArtifactApplication, "research-assets": AssetApplication,
                        "responsibility": ResponsibilityApplication, "assistant": AssistantHistoryApplication,
                        "gui-smoke": GuiSmokeApplication}
        self._modules = {key: constructors[key](session_boundary=self._boundary, clock=clock,
                         **({"connections": config.gui_smoke_connections} if key == "gui-smoke" else {}))
                         for key in config.modules}
        self._artifact_admin = None
        if config.artifact_administration:
            def unavailable_provider(*_):
                # The unified host has no external acquisition configuration.
                raise ProviderError('credential_unavailable')
            sync = ArtifactSynchronizer(session_boundary=self._boundary, clock=clock,
                                        credential_loader=unavailable_provider)
            self._artifact_admin = ArtifactAdministrationAPI(service=sync, allow_refresh=False)
        self._artifact_downloads = None
        if config.artifact_downloads:
            download_service = ArtifactSynchronizer(session_boundary=self._boundary, clock=clock,
                credential_loader=artifact_downloads.credential_loader)
            self._artifact_downloads = ArtifactSessionDownloadAPI(boundary=self._boundary,
                downloader=ArtifactDownloader(service=download_service, transport=artifact_downloads.transport))



    def __call__(self, environ, start_response):
        # The same HTTPS/Host/Cookie/CSRF and error/response envelope protects all
        # mounted routes; module WSGI handlers cannot introduce bearer fallback.
        path = environ.get('PATH_INFO', '')
        parts = path.split('/') if type(path) is str else []
        if (self._artifact_downloads is not None and len(parts) >= 5
                and parts[:3] == ['', 'api', 'workspaces'] and parts[4] == 'artifact-download'):
            # The session-only adapter owns binary response/cleanup, not JSON
            # dispatch. It reuses the same strict request/transaction boundary.
            return self._artifact_downloads(environ, start_response)
        return self._boundary._respond(environ, start_response, dispatch=self._dispatch)

    def _dispatch(self, environ):
        self._boundary._transport_security(environ)
        path = environ.get("PATH_INFO", "")
        if type(path) is not str or not path.startswith("/") or len(path) > 1000:
            raise PolicyError(400, "invalid_request")
        if path == "/" and environ.get("REQUEST_METHOD") == "GET" and not environ.get("QUERY_STRING", ""):
            # Non-sensitive process metadata only, not authorized workspace/module
            # discovery. Membership/capability discovery still goes through API.
            return {"service": "ms-collab", "interface": "configured_service_host", "production_ready": False}
        parts = path.split("/")
        if parts[:3] in (["", "api", "control"], ["", "api", "invitations"]):
            return self._workspaces._dispatch(environ)
        if parts[:3] == ["", "api", "workspaces"]:
            if len(parts) >= 5 and parts[4] == "artifact-admin":
                if self._artifact_admin is None:
                    raise PolicyError(404, "not_found")
                return self._artifact_admin._dispatch(environ)
            if len(parts) >= 5 and parts[4] in _MODULES:
                module = self._modules.get(parts[4])
                if module is None:
                    raise PolicyError(404, "not_found")
                return module._dispatch(environ)
            # Do not expose generic raw resource APIs around a module's finer
            # publication/reader/owner fences. No fallback to legacy demo routes.
            if len(parts) >= 7 and parts[4] == "modules":
                raise PolicyError(404, "not_found")
            return self._workspaces._dispatch(environ)
        raise PolicyError(404, "not_found")
