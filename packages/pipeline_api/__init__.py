"""Configured read-only snapshot API, without live providers or write actions."""
from .application import PipelineApplication
from .records import InvalidRecord, decode_snapshots, encode_snapshots, resource_id

__all__ = ["PipelineApplication", "InvalidRecord", "decode_snapshots", "encode_snapshots", "resource_id"]
