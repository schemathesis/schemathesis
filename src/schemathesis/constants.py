from ._compat import metadata

try:
    __version__ = metadata.version(__package__)
except metadata.PackageNotFoundError:
    # Local run without installation
    __version__ = "dev"


USER_AGENT = f"schemathesis/{__version__}"
