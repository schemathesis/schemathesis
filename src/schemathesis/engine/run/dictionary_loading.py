"""Setup step that fills `from-operation` dictionaries from API responses."""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import TYPE_CHECKING

from schemathesis.config._dictionaries import (
    DictionaryDefinition,
    DictionaryEntry,
    EntryValue,
    OperationSource,
    ParameterDictionaryBinding,
    pair_groups,
    source_bindings,
)
from schemathesis.core import SpecificationFeature
from schemathesis.core.errors import AuthenticationError, DictionaryLoadingError
from schemathesis.core.transforms import Unresolvable, resolve_pointer_all
from schemathesis.engine import events
from schemathesis.engine.run import PhaseName

if TYPE_CHECKING:
    from schemathesis.engine.context import EngineContext
    from schemathesis.schemas import APIOperation

MAX_SOURCE_REQUESTS = 100


@dataclass(slots=True)
class DictionaryLoad:
    dictionary: str
    operation: str
    # Source requests sent.
    requests: int
    # Values the dictionary holds after loading.
    entries: int
    error: str | None = None
    # Requests that errored, out of `requests`.
    failed: int = 0
    # More source requests existed than `MAX_SOURCE_REQUESTS`.
    capped: bool = False


def load_dictionaries(ctx: EngineContext) -> list[DictionaryLoad]:
    """Fill every `from-operation` dictionary in place, after the dictionaries its source parameters use."""
    dictionaries = ctx.schema.config.dictionaries
    # A dependency's origin is a strict subset of its dependent's origin, so dependencies load first.
    ordered = sorted(dictionaries.values(), key=lambda definition: len(definition.origin))
    return [
        _load(ctx, definition, definition.source, dictionaries)
        for definition in ordered
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


def _load(
    ctx: EngineContext,
    definition: DictionaryDefinition,
    source: OperationSource,
    dictionaries: dict[str, DictionaryDefinition],
) -> DictionaryLoad:
    load = DictionaryLoad(dictionary=definition.name, operation=source.operation, requests=0, entries=0)
    if not ctx.schema.specification.supports_feature(SpecificationFeature.OPERATION_DICTIONARIES):
        load.error = "`from-operation` dictionaries need an OpenAPI schema"
        return load
    # The lookup ignores operation filters: a source operation supplies data even when it is not tested.
    operation = ctx.schema.find_operation_by_label(source.operation)
    if operation is None:
        load.error = f"`{source.operation}` is not an operation in the schema"
        return load
    for parameter in operation.path_parameters:
        if f"path.{parameter.name}" not in source.parameters:
            load.error = f"`{source.operation}` needs path parameter `{parameter.name}` - set it under `parameters`"
            return load
    bindings = source_bindings(definition)
    for key, binding in bindings.items():
        if not dictionaries[binding.dictionary].entries:
            load.error = f"source parameter `{key}` has no values: dictionary `{binding.dictionary}` is empty"
            return load
    # Each choice is a value of a bound dictionary together with the values it was fetched with.
    # A dictionary whose values another bound dictionary remembers adds no choices of its own.
    choices = [
        [{leader.name: entry.value} | entry.origin for entry in leader.entries]
        for leader, _ in pair_groups(dictionaries[binding.dictionary] for binding in bindings.values())
    ]
    combinations = list(itertools.islice(itertools.product(*choices), MAX_SOURCE_REQUESTS + 1))
    load.capped = len(combinations) > MAX_SOURCE_REQUESTS
    entries: dict[tuple[object, ...], DictionaryEntry] = {}
    first_error: str | None = None
    for combination in combinations[:MAX_SOURCE_REQUESTS]:
        origin: dict[str, EntryValue] = {}
        for choice in combination:
            origin |= choice
        load.requests += 1
        result = _fetch(ctx, operation, source, origin)
        if isinstance(result, str):
            load.failed += 1
            first_error = first_error or result
            continue
        for value in result:
            identity = (type(value), value, tuple(sorted(origin.items())))
            if identity not in entries:
                entries[identity] = DictionaryEntry(value=value, index=len(entries), origin=origin)
    if not entries:
        load.error = first_error or f"no values at `{source.pointer}` in the `{source.operation}` response"
        return load
    definition.entries = tuple(entries.values())
    load.entries = len(entries)
    return load


def _fetch(
    ctx: EngineContext, operation: APIOperation, source: OperationSource, values: dict[str, EntryValue]
) -> list[EntryValue] | str:
    """Values at the source pointer, or why the request failed."""
    import requests

    from schemathesis.auths import apply_configured_auth

    parameters: dict[str, dict[str, object]] = {"path": {}, "query": {}, "header": {}, "cookie": {}}
    for key, value in source.parameters.items():
        location, _, name = key.partition(".")
        parameters[location][name] = (
            values[value.dictionary] if isinstance(value, ParameterDictionaryBinding) else value
        )
    case = operation.Case(
        path_parameters=parameters["path"],
        query=parameters["query"],
        headers=parameters["header"],
        cookies=parameters["cookie"],
    )
    try:
        apply_configured_auth(case)
        response = case.call(**ctx.get_transport_kwargs(operation=operation))
    except (requests.RequestException, AuthenticationError) as exc:
        return f"`{source.operation}` request failed: {exc}"
    if not 200 <= response.status_code < 300:
        return f"`{source.operation}` returned {response.status_code}"
    try:
        document = response.json()
    except ValueError:
        return f"`{source.operation}` returned a response that is not JSON"
    found = resolve_pointer_all(document, source.pointer)
    if isinstance(found, Unresolvable):
        return []
    # Booleans, objects, arrays and nulls cannot fill a parameter.
    return [value for value in found if isinstance(value, (str, int, float)) and not isinstance(value, bool)]
