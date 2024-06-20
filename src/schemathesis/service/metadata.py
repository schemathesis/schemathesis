"""Useful info to collect from CLI usage."""

from __future__ import annotations

import os
import platform
from dataclasses import dataclass, field
from importlib import metadata

from ..constants import SCHEMATHESIS_VERSION
from .constants import DOCKER_IMAGE_ENV_VAR


@dataclass
class PlatformMetadata:
    # System / OS name, e.g. "Linux" or "Windows".
    system: str = field(default_factory=platform.system)
    # System release, e.g. "5.14" or "NT".
    release: str = field(default_factory=platform.release)
    # Machine type, e.g. "i386".
    machine: str = field(default_factory=platform.machine)


@dataclass
class InterpreterMetadata:
    # The Python version as "major.minor.patch".
    version: str = field(default_factory=platform.python_version)
    # Python implementation, e.g. "CPython" or "PyPy".
    implementation: str = field(default_factory=platform.python_implementation)


@dataclass
class CliMetadata:
    # Schemathesis package version.
    version: str = SCHEMATHESIS_VERSION


DEPDENDENCY_NAMES = ["hypothesis", "hypothesis-jsonschema", "hypothesis-graphql"]


@dataclass
class Dependency:
    """A single dependency."""

    # Name of the package.
    name: str
    # Version of the package.
    version: str

    @classmethod
    def from_name(cls, name: str) -> Dependency:
        return cls(name=name, version=metadata.version(name))


def collect_dependency_versions() -> list[Dependency]:
    return [Dependency.from_name(name) for name in DEPDENDENCY_NAMES]


@dataclass
class Metadata:
    """CLI environment metadata."""

    # Information about the host platform.
    platform: PlatformMetadata = field(default_factory=PlatformMetadata)
    # Python interpreter info.
    interpreter: InterpreterMetadata = field(default_factory=InterpreterMetadata)
    # CLI info itself.
    cli: CliMetadata = field(default_factory=CliMetadata)
    # Used Docker image if any
    docker_image: str | None = field(default_factory=lambda: os.getenv(DOCKER_IMAGE_ENV_VAR))
    depdenencies: list[Dependency] = field(default_factory=collect_dependency_versions)
