from typing import Iterable, Optional

from . import fast_api, utf8_bom

ALL_FIXUPS = {"fast_api": fast_api, "utf8_bom": utf8_bom}
ALL_FIXUP_NAMES = list(ALL_FIXUPS.keys())


def install(fixups: Optional[Iterable[str]] = None) -> None:
    """Install fixups.

    Without the first argument installs all available fixups.

    :param fixups: Names of fixups to install.
    """
    fixups = fixups or ALL_FIXUP_NAMES
    for name in fixups:
        ALL_FIXUPS[name].install()  # type: ignore


def uninstall(fixups: Optional[Iterable[str]] = None) -> None:
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
