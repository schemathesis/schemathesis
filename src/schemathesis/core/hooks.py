import importlib.util
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
        if os.sep in module_path or module_path.endswith(".py"):
            _load_from_file(module_path)
        else:
            sys.path.append(os.getcwd())  # fix ModuleNotFoundError module in cwd
            __import__(module_path)
    except Exception as exc:
        raise HookError(module_path) from exc


def _load_from_file(path: str) -> None:
    spec = importlib.util.spec_from_file_location("_schemathesis_hooks", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load hooks from: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
