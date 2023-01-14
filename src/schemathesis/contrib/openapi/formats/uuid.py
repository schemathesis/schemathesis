from hypothesis import strategies as st

from ....specs import openapi

# Open API 2.0 / 3.0 do not include `uuid` in the list of built-in formats, hence it lives in `contrib`.
FORMAT_NAME = "uuid"


def install() -> None:
    openapi.format(FORMAT_NAME, st.uuids().map(str))


def uninstall() -> None:
    openapi.unregister_string_format("uuid")
