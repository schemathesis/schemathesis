from . import openapi, unique_data


def install() -> None:
    openapi.install()
    unique_data.install()


def uninstall() -> None:
    openapi.uninstall()
    unique_data.uninstall()
