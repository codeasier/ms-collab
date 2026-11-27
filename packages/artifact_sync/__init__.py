"""Bounded read-only OBS acquisition and explicitly approved scoped indexes."""
from .application import IndexedArtifactApplication
from .provider import BoundCredential, Connection, OBSReadTransport, ProviderError
from .records import IndexPolicy, initialize_indexes, regrant_index
from .product_presets import initialize_product_presets
from .service import ArtifactSynchronizer

__all__ = ['ArtifactSynchronizer', 'BoundCredential', 'Connection', 'IndexPolicy',
           'IndexedArtifactApplication', 'OBSReadTransport', 'ProviderError', 'initialize_indexes', 'regrant_index', 'initialize_product_presets']
