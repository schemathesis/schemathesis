from ._compat import _install_hypothesis_jsonschema_compatibility_shim

_install_hypothesis_jsonschema_compatibility_shim()

del _install_hypothesis_jsonschema_compatibility_shim

from . import fixups, hooks, runner, serializers, targets
from .cli import register_check, register_target
from .constants import DataGenerationMethod, __version__
from .models import Case
from .specs import graphql, openapi
from .specs.openapi._hypothesis import init_default_strategies, register_string_format
from .utils import GenericResponse

init_default_strategies()

# Is not a part of the public API
del init_default_strategies

# Default loaders
from_aiohttp = openapi.from_aiohttp
from_asgi = openapi.from_asgi
from_dict = openapi.from_dict
from_file = openapi.from_file
from_path = openapi.from_path
from_pytest_fixture = openapi.from_pytest_fixture
from_uri = openapi.from_uri
from_wsgi = openapi.from_wsgi
