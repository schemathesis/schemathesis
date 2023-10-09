from typing import TYPE_CHECKING

import requests

from ..constants import BOM_MARK
from ..hooks import HookContext
from ..hooks import is_installed as global_is_installed
from ..hooks import register, unregister

if TYPE_CHECKING:
    from .. import Case, GenericResponse


def install() -> None:
    register(after_call)


def uninstall() -> None:
    unregister(after_call)


def is_installed() -> bool:
    return global_is_installed("after_call", after_call)


def after_call(context: HookContext, case: "Case", response: "GenericResponse") -> None:
    if isinstance(response, requests.Response) and response.encoding == "utf-8" and response.text[0:1] == BOM_MARK:
        response.encoding = "utf-8-sig"
