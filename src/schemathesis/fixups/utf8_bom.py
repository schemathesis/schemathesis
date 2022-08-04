from typing import TYPE_CHECKING

from ..constants import BOM_MARK
from ..hooks import HookContext, register, unregister

if TYPE_CHECKING:
    from .. import Case, GenericResponse


def install() -> None:
    register(after_call)


def uninstall() -> None:
    unregister(after_call)


def after_call(context: HookContext, case: "Case", response: "GenericResponse") -> None:
    if response.encoding == "utf-8" and response.text[0:1] == BOM_MARK:
        response.encoding = "utf-8-sig"
