from importlib_metadata import version

from .loaders import Parametrizer, from_dict, from_file, from_path, from_pytest_fixture, from_uri
from .models import Case

__version__ = version(__package__)
