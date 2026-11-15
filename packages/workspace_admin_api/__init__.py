"""Explicitly configured workspace administration, without a default identity."""
from .application import ModuleTemplate, WorkspaceAdministrationApplication
from .requests import initialize_administration

__all__ = ["ModuleTemplate", "WorkspaceAdministrationApplication", "initialize_administration"]
