"""Constants extraction from Python source.

Internal API. Behavior and signatures may change between minor versions; not
advertised in main docs. Register sources from a hooks module:

    import schemathesis

    @schemathesis.python.constants
    def my_modules():
        from my_app import app
        return app
"""

from schemathesis.python._constants.pool import ConstantEntry, ConstantsPool, ConstantsValueSource, Origin
from schemathesis.python._constants.registry import constants

__all__ = ["ConstantEntry", "ConstantsPool", "ConstantsValueSource", "Origin", "constants"]
