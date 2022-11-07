# pylint: disable=ungrouped-imports
import hypothesis
import hypothesis_jsonschema._from_schema
import jsonschema
import werkzeug
from hypothesis import strategies as st
from hypothesis.errors import InvalidArgument
from packaging import version

# pylint: disable=unused-import

try:
    from importlib import metadata
except ImportError:
    import importlib_metadata as metadata  # type: ignore

if version.parse(werkzeug.__version__) < version.parse("2.1.0"):
    from werkzeug.wrappers.json import JSONMixin
else:

    class JSONMixin:  # type: ignore
        pass


HYPOTHESIS_VERSION = version.parse(hypothesis.__version__)
IS_HYPOTHESIS_ABOVE_6_49 = HYPOTHESIS_VERSION >= version.parse("6.49.0")
IS_HYPOTHESIS_ABOVE_6_54 = HYPOTHESIS_VERSION >= version.parse("6.54.0")

try:
    from hypothesis.utils.conventions import InferType
except ImportError:
    InferType = type(...)

if IS_HYPOTHESIS_ABOVE_6_49:
    from hypothesis.internal.reflection import get_signature
else:
    from inspect import getfullargspec as get_signature

try:
    MultipleFailures = BaseExceptionGroup  # type: ignore
except NameError:
    if IS_HYPOTHESIS_ABOVE_6_54:
        from exceptiongroup import BaseExceptionGroup as MultipleFailures  # type: ignore
    else:
        from hypothesis.errors import MultipleFailures  # type: ignore


def _get_format_filter(
    format_name: str,
    checker: jsonschema.FormatChecker,
    strategy: st.SearchStrategy[str],
) -> st.SearchStrategy[str]:
    def check_valid(string: str) -> str:
        try:
            checker.check(string, format=format_name)
        except jsonschema.FormatError as err:
            raise InvalidArgument(
                f"Got string={string!r} from strategy {strategy!r}, but this "
                f"is not a valid value for the {format_name!r} checker."
            ) from err
        return string

    return strategy.map(check_valid)


def _install_hypothesis_jsonschema_compatibility_shim() -> None:
    """Monkey patch ``hypothesis-jsonschema`` for compatibility reasons.

    Open API < 3.1 uses ``binary`` or ``file`` values for the ``format`` keyword, intended to be used in
    the non-JSON context of binary data. As hypothesis-jsonschema follows the JSON Schema semantic, formats that imply
    non-string values are invalid.

    Note that this solution is temporary.
    """
    hypothesis_jsonschema._from_schema._get_format_filter = _get_format_filter
