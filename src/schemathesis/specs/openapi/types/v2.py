from __future__ import annotations
from typing import Literal, TypedDict

from .common import Reference, Operation as OperationBase


class Specification(TypedDict):
    swagger: Literal["2.0"]


class Operation(OperationBase):
    parameters: list[Parameter | Reference]


NonBodyParameter = TypedDict(
    "NonBodyParameter",
    {
        "name": str,
        "in": Literal["query", "header", "path", "formData"],
        "required": bool,
    },
)
BodyParameter = TypedDict(
    "BodyParameter",
    {
        "name": str,
        "in": Literal["body"],
        "required": bool,
        "schema": dict,
    },
)

Parameter = NonBodyParameter | BodyParameter
