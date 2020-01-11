from typing import Optional

from .constants import HookLocation
from .types import Hook

GLOBAL_HOOKS = {}


def register(place: str, hook: Hook) -> None:
    key = HookLocation[place]
    GLOBAL_HOOKS[key] = hook


def get_hook(place: str) -> Optional[Hook]:
    key = HookLocation[place]
    return GLOBAL_HOOKS.get(key)


def unregister_all() -> None:
    GLOBAL_HOOKS.clear()
