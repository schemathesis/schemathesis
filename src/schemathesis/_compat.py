__all__ = ["BaseExceptionGroup"]

try:
    BaseExceptionGroup = BaseExceptionGroup  # type: ignore
except NameError:
    from exceptiongroup import BaseExceptionGroup  # type: ignore
