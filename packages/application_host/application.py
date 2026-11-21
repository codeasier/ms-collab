"""A fixed route registry sharing the same session/policy transaction authority."""
from dataclasses import dataclass
from datetime import datetime, timezone

from packages.assistant_history import AssistantHistoryApplication
from packages.gui_smoke import GuiSmokeApplication, SmokeConnection
from packages.artifact_sync import IndexedArtifactApplication, ArtifactSynchronizer, ProviderError
from packages.artifact_admin import ArtifactAdministrationAPI
from packages.artifact_refresh import ArtifactRefreshConfiguration, ConfiguredArtifactRefresh
from packages.artifact_download import ArtifactDownloader, ArtifactDownloadConfiguration, ArtifactSessionDownloadAPI
from packages.community_api import CommunityApplication
from packages.community_refresh import CommunityRefreshConfiguration, ConfiguredCommunityRefresh
from packages.pipeline_api import PipelineApplication
from packages.research_assets import AssetApplication
from packages.responsibility_api import ResponsibilityApplication
from packages.session_api import SessionPolicyStore, SessionWorkspaceApplication
from packages.workspace_api import WorkspaceApplication
from packages.workspace_admin_api import ModuleTemplate, WorkspaceAdministrationApplication
from packages.workspace_invitations import WorkspaceInvitationApplication
from packages.workspace_connections import CommunityConnectionTemplate, WorkspaceConnectionsApplication
from packages.workspace_connections.records import templates as connection_templates
from packages.workspaces import PolicyError
from packages.directory_cursors.directory import configured

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
    artifact_refresh: bool = False
    community_connections: tuple[CommunityConnectionTemplate, ...] = ()
    community_refresh: bool = False
    responsibility_suggestions: bool = False

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
        if type(self.community_refresh) is not bool or (self.community_refresh and "community" not in self.modules):
            raise ValueError("community refresh requires its explicit hosted module")
        connection_templates(self.community_connections)
        if self.community_connections and "community" not in self.modules:
            raise ValueError("connection management requires its explicit community module")
        if type(self.artifact_refresh) is not bool or (self.artifact_refresh and not self.artifact_administration):
            raise ValueError('artifact refresh requires explicit administration')
        if (type(self.responsibility_suggestions) is not bool
                or self.responsibility_suggestions and "responsibility" not in self.modules):
            raise ValueError("suggestions require the explicit responsibility module")
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
                 clock=lambda: datetime.now(timezone.utc), artifact_downloads=None, artifact_refresh=None,
                 community_refresh=None, directory_cursors=None, admin_transfers=None,
                 artifact_product_views=None, responsibility_suggestions=None):
        if type(config) is not HostConfig or type(sessions) is not SessionPolicyStore or not callable(clock):
            raise ValueError("explicit host configuration, initialized stores and clock required")
        if ("artifacts" not in config.modules and artifact_product_views is not None
                and (type(artifact_product_views) not in (tuple, list) or artifact_product_views)):
            raise ValueError("product views require the configured artifact index")
        if (config.artifact_downloads and type(artifact_downloads) is not ArtifactDownloadConfiguration
                or not config.artifact_downloads and artifact_downloads is not None):
            raise ValueError('explicit download switch and trusted configuration required together')
        if (config.artifact_refresh and type(artifact_refresh) is not ArtifactRefreshConfiguration
                or not config.artifact_refresh and artifact_refresh is not None):
            raise ValueError('explicit refresh switch and trusted configuration required together')
        if (config.community_refresh and type(community_refresh) is not CommunityRefreshConfiguration
                or not config.community_refresh and community_refresh is not None):
            raise ValueError("explicit community refresh switch and trusted configuration required together")
        if (config.responsibility_suggestions and (type(responsibility_suggestions) is not tuple or not responsibility_suggestions)
                or not config.responsibility_suggestions and responsibility_suggestions is not None):
            raise ValueError("explicit suggestion switch and prepared batches required together")
        self._directory_cursors = configured(directory_cursors)
        self._config = config
        self._boundary = SessionWorkspaceApplication(sessions=sessions, origin=config.origin,
            exchange=exchange, authorization_endpoint=authorization_endpoint,
            authorization_scopes=authorization_scopes)
        authority = WorkspaceApplication(session_boundary=self._boundary)
        administration = WorkspaceAdministrationApplication(api=authority, module_catalog=config.module_catalog,
            transfers=admin_transfers)
        self._connections = WorkspaceConnectionsApplication(api=authority, connections=config.community_connections) if config.community_connections else None
        self._workspaces = WorkspaceInvitationApplication(api=administration, clock=clock)
        constructors = {"community": CommunityApplication, "pipelines": PipelineApplication,
                        "artifacts": IndexedArtifactApplication, "research-assets": AssetApplication,
                        "responsibility": ResponsibilityApplication, "assistant": AssistantHistoryApplication,
                        "gui-smoke": GuiSmokeApplication}
        self._modules = {key: constructors[key](session_boundary=self._boundary, clock=clock,
                         **({"directory_cursors": self._directory_cursors, "product_views": artifact_product_views} if key == "artifacts" else
                            {"directory_cursors": self._directory_cursors} if key == "pipelines" else
                            {"connections": config.gui_smoke_connections} if key == "gui-smoke" else
                            {"prepared_suggestions": responsibility_suggestions} if key == "responsibility" and config.responsibility_suggestions else
                            {"credential_loader": community_refresh.credential_loader} if key == "community" and config.community_refresh else {}))
                         for key in config.modules}
        if config.community_refresh:
            community = self._modules["community"]
            community._refresher = ConfiguredCommunityRefresh(service=community, transport=community_refresh.transport)
        self._artifact_admin = None
        if config.artifact_administration:
            def unavailable_provider(*_):
                # The default host has no external acquisition configuration.
                raise ProviderError('credential_unavailable')
            sync = ArtifactSynchronizer(session_boundary=self._boundary, clock=clock,
                                        credential_loader=artifact_refresh.credential_loader if config.artifact_refresh else unavailable_provider)
            refresher = ConfiguredArtifactRefresh(service=sync, transport=artifact_refresh.transport) if config.artifact_refresh else None
            self._artifact_admin = ArtifactAdministrationAPI(service=sync, allow_refresh=config.artifact_refresh, refresher=refresher, directory_cursors=self._directory_cursors)
        self._artifact_downloads = None
        if config.artifact_downloads:
            download_service = ArtifactSynchronizer(session_boundary=self._boundary, clock=clock,
                credential_loader=artifact_downloads.credential_loader)
            self._artifact_downloads = ArtifactSessionDownloadAPI(boundary=self._boundary,
                downloader=ArtifactDownloader(service=download_service, transport=artifact_downloads.transport))

    @property
    def artifact_product_views_enabled(self):
        """Host UI support only, never a scope, publication or reader grant."""
        index = self._modules.get("artifacts")
        return index is not None and bool(index._product_views)

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
            if len(parts) >= 5 and parts[4] == "connections":
                if self._connections is None:
                    raise PolicyError(404, "not_found")
                return self._connections._dispatch(environ)
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
