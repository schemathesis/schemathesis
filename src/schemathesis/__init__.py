from ._compat import _install_hypothesis_jsonschema_compatibility_shim

_install_hypothesis_jsonschema_compatibility_shim()

del _install_hypothesis_jsonschema_compatibility_shim

from . import auths, checks, contrib, fixups, graphql, hooks, runner, serializers, targets  # noqa: E402
from .constants import DataGenerationMethod, __version__  # noqa: E402
from .models import Case  # noqa: E402
from .specs import openapi  # noqa: E402
from .specs.openapi._hypothesis import init_default_strategies  # noqa: E402
from .utils import GenericResponse  # noqa: E402

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

# Public API
auth = auths.register
check = checks.register
hook = hooks.register
serializer = serializers.register
target = targets.register

# Backward compatibility
register_check = checks.register
register_target = targets.register
register_string_format = openapi.format

auth.__dict__["register"] = auths.register
