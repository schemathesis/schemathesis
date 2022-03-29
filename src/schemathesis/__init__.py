from ._compat import _install_hypothesis_jsonschema_compatibility_shim

_install_hypothesis_jsonschema_compatibility_shim()

del _install_hypothesis_jsonschema_compatibility_shim
from . import fixups, hooks, targets
from .cli import register_check, register_target
from .constants import DataGenerationMethod, __version__
from .loaders import from_asgi, from_dict, from_file, from_path, from_pytest_fixture, from_uri, from_wsgi
from .models import Case
from .specs import graphql
from .specs.openapi._hypothesis import init_default_strategies, register_string_format
from .stateful import Stateful
from .utils import GenericResponse

init_default_strategies()
