try:
    BaseExceptionGroup = BaseExceptionGroup  # type: ignore
except NameError:
    from exceptiongroup import BaseExceptionGroup  # type: ignore

# Import it just once to keep just a single warning
from jsonschema import RefResolutionError

__all__ = ["BaseExceptionGroup", "RefResolutionError"]
