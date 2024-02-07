"""Compatibility flags based on installed dependency versions."""
from packaging import version

from importlib import metadata


WERKZEUG_VERSION = version.parse(metadata.version("werkzeug"))
IS_WERKZEUG_ABOVE_3 = WERKZEUG_VERSION >= version.parse("3.0")
IS_WERKZEUG_BELOW_2_1 = WERKZEUG_VERSION < version.parse("2.1.0")

PYTEST_VERSION = version.parse(metadata.version("pytest"))
IS_PYTEST_ABOVE_54 = PYTEST_VERSION >= version.parse("5.4.0")
IS_PYTEST_ABOVE_7 = PYTEST_VERSION >= version.parse("7.0.0")
IS_PYTEST_ABOVE_8 = PYTEST_VERSION >= version.parse("8.0.0")
