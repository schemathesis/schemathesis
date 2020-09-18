from ._compat import metadata

try:
    __version__ = metadata.version(__package__)
except metadata.PackageNotFoundError:
    # Local run without installation
    __version__ = "dev"


USER_AGENT = f"schemathesis/{__version__}"
DEFAULT_DEADLINE = 500  # pragma: no mutate
DEFAULT_STATEFUL_RECURSION_LIMIT = 5  # pragma: no mutate
