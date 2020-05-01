import inspect
import warnings
from typing import Optional

import attr

from .constants import HookLocation
from .models import Endpoint
from .types import Hook

GLOBAL_HOOKS = {}


@attr.s(slots=True)
class HookContext:
    """A context that is passed to hook functions."""

    endpoint: Endpoint = attr.ib()


def warn_deprecated_hook(hook: Hook) -> None:
    if "context" not in inspect.signature(hook).parameters:
        warnings.warn(
            DeprecationWarning(
                "Hook functions that do not accept `context` argument are deprecated and "
                "support will be removed in Schemathesis 2.0."
            )
        )


def register(place: str, hook: Hook) -> None:
    warn_deprecated_hook(hook)
    key = HookLocation[place]
    GLOBAL_HOOKS[key] = hook


def get_hook(place: str) -> Optional[Hook]:
    key = HookLocation[place]
    return GLOBAL_HOOKS.get(key)


def unregister_all() -> None:
    GLOBAL_HOOKS.clear()
