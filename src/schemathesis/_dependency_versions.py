"""Compatibility flags based on installed dependency versions."""
from packaging import version

from importlib import metadata


WERKZEUG_VERSION = version.parse(metadata.version("werkzeug"))
IS_WERKZEUG_ABOVE_3 = WERKZEUG_VERSION >= version.parse("3.0")
IS_WERKZEUG_BELOW_2_1 = WERKZEUG_VERSION < version.parse("2.1.0")

HYPOTHESIS_VERSION = version.parse(metadata.version("hypothesis"))
IS_HYPOTHESIS_ABOVE_6_49 = HYPOTHESIS_VERSION >= version.parse("6.49.0")
IS_HYPOTHESIS_ABOVE_6_54 = HYPOTHESIS_VERSION >= version.parse("6.54.0")
IS_HYPOTHESIS_ABOVE_6_68_1 = HYPOTHESIS_VERSION >= version.parse("6.68.1")

PYTEST_VERSION = version.parse(metadata.version("pytest"))
IS_PYTEST_ABOVE_54 = PYTEST_VERSION >= version.parse("5.4.0")
IS_PYTEST_ABOVE_7 = PYTEST_VERSION >= version.parse("7.0.0")
