"""Scoped read-only upstream acquisition, with separately authorized local writes."""
from .provider import BoundCredential, Connection, HTTPSReadTransport, ProviderError
from .service import PipelineSynchronizer, connection_record, connection_resource_id

__all__ = ["BoundCredential", "Connection", "HTTPSReadTransport", "ProviderError", "PipelineSynchronizer",
           "connection_record", "connection_resource_id"]
