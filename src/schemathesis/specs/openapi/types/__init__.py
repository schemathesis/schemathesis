from typing import TypeAlias

from schemathesis.specs.openapi.types import common, v2, v3

# Operation object of either OpenAPI family, for code reading fields common to v2 and v3 (operationId, tags).
OperationObject: TypeAlias = v3.Operation | v2.Operation

__all__ = ["OperationObject", "common", "v2", "v3"]
