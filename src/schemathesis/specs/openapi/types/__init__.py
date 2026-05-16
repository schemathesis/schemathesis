from typing import TypeAlias

from schemathesis.specs.openapi.types import common, v2, v3

# Operation object across both supported OpenAPI families. Used by spec-agnostic
# operation-construction paths that read fields common to v2 and v3 (operationId, tags, etc.).
OperationObject: TypeAlias = v3.Operation | v2.Operation

__all__ = ["OperationObject", "common", "v2", "v3"]
