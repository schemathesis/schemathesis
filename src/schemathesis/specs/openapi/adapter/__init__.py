from schemathesis.specs.openapi.adapter import v2, v3_0, v3_1
from schemathesis.specs.openapi.adapter.responses import OpenApiResponse, OpenApiResponses

# OpenAPI 3.2 uses the same adapter as 3.1
v3_2 = v3_1

__all__ = [
    "OpenApiResponse",
    "OpenApiResponses",
    "v2",
    "v3_0",
    "v3_1",
    "v3_2",
]
