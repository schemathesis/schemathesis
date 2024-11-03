from importlib import metadata

try:
    SCHEMATHESIS_VERSION = metadata.version(__package__)
except metadata.PackageNotFoundError:
    # Local run without installation
    SCHEMATHESIS_VERSION = "dev"
