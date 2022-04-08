"""Useful info to collect from CLI usage."""
import platform

import attr

from ..constants import __version__


@attr.s(slots=True)
class PlatformMetadata:
    # System / OS name, e.g. "Linux" or "Windows".
    system: str = attr.ib(factory=platform.system)
    # System release, e.g. "5.14" or "NT".
    release: str = attr.ib(factory=platform.release)
    # Machine type, e.g. "i386".
    machine: str = attr.ib(factory=platform.machine)


@attr.s(slots=True)
class InterpreterMetadata:
    # The Python version as "major.minor.patch".
    version: str = attr.ib(factory=platform.python_version)
    # Python implementation, e.g. "CPython" or "PyPy".
    implementation: str = attr.ib(factory=platform.python_implementation)


@attr.s(slots=True)
class CliMetadata:
    # Schemathesis package version.
    version: str = attr.ib(default=__version__)


@attr.s(slots=True)
class Metadata:
    """CLI environment metadata."""

    # Information about the host platform.
    platform: PlatformMetadata = attr.ib(factory=PlatformMetadata)
    # Python interpreter info.
    interpreter: InterpreterMetadata = attr.ib(factory=InterpreterMetadata)
    # CLI info itself.
    cli: CliMetadata = attr.ib(factory=CliMetadata)
