# pylint: disable=unused-import
try:
    from importlib import metadata
except ImportError:
    import importlib_metadata as metadata  # type: ignore
