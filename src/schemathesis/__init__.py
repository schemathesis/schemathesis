from . import auths, checks, experimental, contrib, fixups, graphql, hooks, runner, serializers, targets  # noqa: E402
from .constants import DataGenerationMethod, SCHEMATHESIS_VERSION  # noqa: E402
from .models import Case  # noqa: E402
from .specs import openapi  # noqa: E402
from .utils import GenericResponse  # noqa: E402


__version__ = SCHEMATHESIS_VERSION

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
auth = auths.GLOBAL_AUTH_STORAGE
check = checks.register
hook = hooks.register
serializer = serializers.register
target = targets.register

# Backward compatibility
register_check = checks.register
register_target = targets.register
register_string_format = openapi.format
