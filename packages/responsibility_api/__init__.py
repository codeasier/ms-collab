"""Explicitly configured scope authorization for responsibility candidates."""
from .application import ResponsibilityApplication
from .records import ResponsibilityPolicy,initialize,replace_policy
__all__=['ResponsibilityApplication','ResponsibilityPolicy','initialize','replace_policy']
