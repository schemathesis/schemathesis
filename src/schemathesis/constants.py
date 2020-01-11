from enum import Enum

from importlib_metadata import PackageNotFoundError, version

try:
    __version__ = version(__package__)
except PackageNotFoundError:
    # Local run without installation
    __version__ = "dev"


USER_AGENT = f"schemathesis/{__version__}"


class HookLocation(Enum):
    path_parameters = 1
    headers = 2
    cookies = 3
    query = 4
    body = 5
    form_data = 6
