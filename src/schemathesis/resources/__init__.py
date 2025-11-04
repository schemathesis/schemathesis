from __future__ import annotations

from .descriptors import Cardinality, ResourceDescriptor
from .repository import ResourceInstance, ResourceRepository, ResourceRepositoryConfig

__all__ = [
    "Cardinality",
    "ResourceDescriptor",
    "ResourceInstance",
    "ResourceRepository",
    "ResourceRepositoryConfig",
]
