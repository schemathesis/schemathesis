# pylint: disable=unused-import
import werkzeug
from packaging import version

try:
    from importlib import metadata
except ImportError:
    import importlib_metadata as metadata  # type: ignore

if version.parse(werkzeug.__version__) < version.parse("2.1.0"):
    from werkzeug.wrappers.json import JSONMixin
else:

    class JSONMixin:  # type: ignore
        pass
