from typing import Iterable, List, Optional

from . import fast_api

ALL_FIXUPS = {"fast_api": fast_api}


def install(fixups: Optional[Iterable[str]] = None) -> None:
    """Install fixups.

    Without the first argument installs all available fixups.
    """
    fixups = fixups or list(ALL_FIXUPS.keys())
    for name in fixups:
        ALL_FIXUPS[name].install()  # type: ignore


def uninstall(fixups: Optional[Iterable[str]] = None) -> None:
    """Uninstall fixups.

    Without the first argument uninstalls all available fixups.
    """
    fixups = fixups or list(ALL_FIXUPS.keys())
    for name in fixups:
        ALL_FIXUPS[name].uninstall()  # type: ignore
