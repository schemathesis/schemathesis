from typing import Any, Callable, Dict, Optional, Union

import attr

from . import readers, schemas, types

NOT_SET = object()


@attr.s(slots=True)
class Holder:
    """A holder to store evaluated closures."""

    closure: Callable = attr.ib(default=NOT_SET)  # type: ignore
    value: Any = attr.ib(default=NOT_SET)

    def get(self, *args: Any, **kwargs: Any) -> Any:
        if self.value is NOT_SET:
            self.value = self.closure(*args, **kwargs)  # pylint: disable=not-callable
        return self.value


RawSchema = Union[Holder, Callable, Dict[str, Any]]


def prepare_schema(schema: RawSchema) -> Holder:
    """Prepare the given schema to bbe stored in parametrizer.

    It should be stored in a `Holder` instance to reuse evaluated lazy schemas.
    """
    if isinstance(schema, Holder):  # pylint: disable=no-else-return
        return schema
    elif callable(schema):
        return Holder(closure=schema)
    return Holder(value=schema)


class SchemaParametrizer:
    """An entry point for test parametrization.

    Store parametrization config and mark test functions for further processing.
    """

    __slots__ = ("raw_schema", "filter_method", "filter_endpoint", "hypothesis_settings", "_schema")

    def __init__(
        self,
        raw_schema: RawSchema,
        filter_method: Optional[types.Filter] = None,
        filter_endpoint: Optional[types.Filter] = None,
        **kwargs: Any
    ) -> None:
        self.raw_schema = prepare_schema(raw_schema)
        self.filter_method = filter_method
        self.filter_endpoint = filter_endpoint
        self.hypothesis_settings = kwargs
        self._schema: Optional[schemas.BaseSchema] = None

    @classmethod
    def from_path(cls, path: str) -> "SchemaParametrizer":
        """Create a parametrizer from the given OS path."""
        return cls(lambda: readers.from_path(path))

    @property
    def schema(self) -> schemas.BaseSchema:
        """A cached schema abstraction to use in parametrization."""
        if self._schema is None:
            schema = self.raw_schema.get()
            self._schema = schemas.wrap_schema(schema)
        return self._schema

    def parametrize(
        self,
        filter_method: Optional[types.Filter] = None,
        filter_endpoint: Optional[types.Filter] = None,
        **kwargs: Any
    ) -> Callable:
        """Mark a test function as a parametrized one.

        Create a copy of the current parametrization, but with updated parameters and re-using the schema.
        """

        def wrapper(func: Callable) -> Callable:
            func._schema_parametrizer = self.__class__(  # type: ignore
                raw_schema=self.raw_schema,
                filter_method=filter_method,
                filter_endpoint=filter_endpoint,
                **{**self.hypothesis_settings, **kwargs}
            )
            return func

        return wrapper


def is_schemathesis_test(func: Callable) -> bool:
    """Check whether test is parametrized with schemathesis."""
    return hasattr(func, "_schema_parametrizer")
