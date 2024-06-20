from typing import TYPE_CHECKING

from ..constants import BOM_MARK
from ..hooks import HookContext, register, unregister
from ..hooks import is_installed as global_is_installed

if TYPE_CHECKING:
    from ..models import Case
    from ..transports.responses import GenericResponse


def install() -> None:
    register(after_call)


def uninstall() -> None:
    unregister(after_call)


def is_installed() -> bool:
    return global_is_installed("after_call", after_call)


def after_call(context: HookContext, case: "Case", response: "GenericResponse") -> None:
    from requests import Response

    if isinstance(response, Response) and response.encoding == "utf-8" and response.text[0:1] == BOM_MARK:
        response.encoding = "utf-8-sig"
