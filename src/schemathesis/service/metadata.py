"""Useful info to collect from CLI usage."""
import platform
from typing import Any, Dict

from ..constants import __version__


def collect() -> Dict[str, Any]:
    """Collect environment metadata."""
    return {
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "interpreter": {"version": platform.python_version(), "implementation": platform.python_implementation()},
        "cli": {"version": __version__},
    }
