"""Explicitly configured workspace administration, without a default identity."""
from .application import ModuleTemplate, WorkspaceAdministrationApplication
from .requests import initialize_administration, migrate_administration_v1

__all__ = ["ModuleTemplate", "WorkspaceAdministrationApplication", "initialize_administration", "migrate_administration_v1"]
