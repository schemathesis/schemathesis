from __future__ import annotations

from typing import Iterable

from . import utf8_bom

ALL_FIXUPS = {"utf8_bom": utf8_bom}
ALL_FIXUP_NAMES = list(ALL_FIXUPS.keys())


def install(fixups: Iterable[str] | None = None) -> None:
    """Install fixups.

    Without the first argument installs all available fixups.

    :param fixups: Names of fixups to install.
    """
    fixups = fixups or ALL_FIXUP_NAMES
    for name in fixups:
        ALL_FIXUPS[name].install()  # type: ignore


def uninstall(fixups: Iterable[str] | None = None) -> None:
    """Uninstall fixups.

    Without the first argument uninstalls all available fixups.

    :param fixups: Names of fixups to uninstall.
    """
    fixups = fixups or ALL_FIXUP_NAMES
    for name in fixups:
        ALL_FIXUPS[name].uninstall()  # type: ignore


def is_installed(name: str) -> bool:
    """Check whether fixup is installed."""
    return ALL_FIXUPS[name].is_installed()
