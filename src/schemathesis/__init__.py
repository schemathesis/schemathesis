from importlib_metadata import PackageNotFoundError, version

from .loaders import Parametrizer, from_dict, from_file, from_path, from_pytest_fixture, from_uri
from .models import Case

try:
    __version__ = version(__package__)
except PackageNotFoundError:
    # Local run without installation
    __version__ = "dev"
