from __future__ import annotations

try:
    BaseExceptionGroup = BaseExceptionGroup
except NameError:
    from exceptiongroup import BaseExceptionGroup


__all__ = ["BaseExceptionGroup"]
