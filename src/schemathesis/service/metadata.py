"""Useful info to collect from CLI usage."""
import platform
from dataclasses import dataclass, field

from ..constants import __version__


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
    version: str = __version__


@dataclass
class Metadata:
    """CLI environment metadata."""

    # Information about the host platform.
    platform: PlatformMetadata = field(default_factory=PlatformMetadata)
    # Python interpreter info.
    interpreter: InterpreterMetadata = field(default_factory=InterpreterMetadata)
    # CLI info itself.
    cli: CliMetadata = field(default_factory=CliMetadata)
