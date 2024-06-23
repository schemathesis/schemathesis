"""Compatibility flags based on installed dependency versions."""

from importlib import metadata

from packaging import version

WERKZEUG_VERSION = version.parse(metadata.version("werkzeug"))
IS_WERKZEUG_ABOVE_3 = WERKZEUG_VERSION >= version.parse("3.0")
IS_WERKZEUG_BELOW_2_1 = WERKZEUG_VERSION < version.parse("2.1.0")

PYTEST_VERSION = version.parse(metadata.version("pytest"))
IS_PYTEST_ABOVE_7 = PYTEST_VERSION >= version.parse("7.0.0")
IS_PYTEST_ABOVE_8 = PYTEST_VERSION >= version.parse("8.0.0")

HYPOTHESIS_VERSION = version.parse(metadata.version("hypothesis"))
HYPOTHESIS_HAS_STATEFUL_NAMING_IMPROVEMENTS = HYPOTHESIS_VERSION >= version.parse("6.98.14")

PYRATE_LIMITER_VERSION = version.parse(metadata.version("pyrate-limiter"))
IS_PYRATE_LIMITER_ABOVE_3 = PYRATE_LIMITER_VERSION >= version.parse("3.0")
