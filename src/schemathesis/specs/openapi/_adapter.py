from __future__ import annotations

from typing import Any, Iterator, Mapping, TypedDict

from schemathesis.core.bundler import bundle
from schemathesis.core.compat import RefResolver

PathItem = Mapping[str, Any]
Operation = TypedDict("Operation", {"responses": dict})


def prepare_parameters(item: PathItem | Operation, resolver: RefResolver) -> Iterator[dict]:
    parameters = item.get("parameters", [])
    assert isinstance(parameters, list)
    for parameter in parameters:
        ref = parameter.get("$ref")
        if ref is not None:
            (_, resolved) = resolver.resolve(ref)
            definition = resolved
        else:
            definition = parameter
        schema = definition.get("schema")
        if schema is not None:
            # Copy the definition and bundle the schema to make it self-contained
            definition = {k: v for k, v in definition.items() if k != "schema"}
            definition["schema"] = bundle(schema, resolver)
        yield definition
