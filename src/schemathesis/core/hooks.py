import os
import sys

from schemathesis.core.errors import HookError

HOOKS_MODULE_ENV_VAR = "SCHEMATHESIS_HOOKS"


def load_from_env() -> None:
    hooks = os.getenv(HOOKS_MODULE_ENV_VAR)
    if hooks:
        load_from_path(hooks)


def load_from_path(module_path: str) -> None:
    try:
        sys.path.append(os.getcwd())  # fix ModuleNotFoundError module in cwd
        __import__(module_path)
    except Exception as exc:
        raise HookError(module_path) from exc
