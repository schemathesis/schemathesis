"""Setup step that fills `from-operation` dictionaries from API responses."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from schemathesis.config._dictionaries import DictionaryDefinition, DictionaryEntry, OperationSource
from schemathesis.core import SpecificationFeature
from schemathesis.core.errors import AuthenticationError, DictionaryLoadingError
from schemathesis.core.transforms import Unresolvable, resolve_pointer_all
from schemathesis.engine import events
from schemathesis.engine.run import PhaseName

if TYPE_CHECKING:
    from schemathesis.engine.context import EngineContext


@dataclass(slots=True)
class DictionaryLoad:
    dictionary: str
    operation: str
    # Source requests sent.
    requests: int
    # Values the dictionary holds after loading.
    entries: int
    error: str | None = None


def load_dictionaries(ctx: EngineContext) -> list[DictionaryLoad]:
    """Fill every `from-operation` dictionary in place, in configuration order."""
    return [
        _load(ctx, definition, definition.source)
        for definition in ctx.schema.config.dictionaries.values()
        if definition.source is not None
    ]


def load_errors(loads: list[DictionaryLoad]) -> list[events.NonFatalError]:
    return [
        events.NonFatalError(
            error=DictionaryLoadingError(f"Dictionary `{load.dictionary}`: {load.error}"),
            phase=PhaseName.SETUP,
            label=f"Dictionary `{load.dictionary}`",
            related_to_operation=False,
        )
        for load in loads
        if load.error is not None
    ]


def _load(ctx: EngineContext, definition: DictionaryDefinition, source: OperationSource) -> DictionaryLoad:
    import requests

    from schemathesis.auths import apply_configured_auth

    load = DictionaryLoad(dictionary=definition.name, operation=source.operation, requests=0, entries=0)
    if not ctx.schema.specification.supports_feature(SpecificationFeature.OPERATION_DICTIONARIES):
        load.error = "`from-operation` dictionaries need an OpenAPI schema"
        return load
    # The lookup ignores operation filters: a source operation supplies data even when it is not tested.
    operation = ctx.schema.find_operation_by_label(source.operation)
    if operation is None:
        load.error = f"`{source.operation}` is not an operation in the schema"
        return load
    parameters: dict[str, dict[str, object]] = {"path": {}, "query": {}, "header": {}, "cookie": {}}
    for key, value in source.parameters.items():
        location, _, name = key.partition(".")
        parameters[location][name] = value
    for parameter in operation.path_parameters:
        if parameter.name not in parameters["path"]:
            load.error = f"`{source.operation}` needs path parameter `{parameter.name}` - set it under `parameters`"
            return load
    case = operation.Case(
        path_parameters=parameters["path"],
        query=parameters["query"],
        headers=parameters["header"],
        cookies=parameters["cookie"],
    )
    try:
        apply_configured_auth(case)
        load.requests += 1
        response = case.call(**ctx.get_transport_kwargs(operation=operation))
    except (requests.RequestException, AuthenticationError) as exc:
        load.error = f"`{source.operation}` request failed: {exc}"
        return load
    if not 200 <= response.status_code < 300:
        load.error = f"`{source.operation}` returned {response.status_code}"
        return load
    try:
        document = response.json()
    except ValueError:
        load.error = f"`{source.operation}` returned a response that is not JSON"
        return load
    found = resolve_pointer_all(document, source.pointer)
    # Booleans, objects, arrays and nulls cannot fill a parameter.
    values = (
        []
        if isinstance(found, Unresolvable)
        else [value for value in found if isinstance(value, (str, int, float)) and not isinstance(value, bool)]
    )
    if not values:
        load.error = f"no values at `{source.pointer}` in the `{source.operation}` response"
        return load
    definition.entries = tuple(DictionaryEntry(value=value, index=index) for index, value in enumerate(values))
    load.entries = len(values)
    return load
