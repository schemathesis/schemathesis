from __future__ import annotations

from typing import TYPE_CHECKING

from schemathesis.core.marks import Mark

if TYPE_CHECKING:
    from schemathesis.core.errors import InvalidSchema
    from schemathesis.generation.drivers import Controller
    from schemathesis.schemas import APIOperation

ExamplesControllerMark: Mark[Controller] = Mark(attr_name="examples_controller")
UnserializablePayloadMark: Mark[list[str]] = Mark(attr_name="unserializable_payload")
MissingPathParameters: Mark[InvalidSchema] = Mark(attr_name="missing_path_parameters")
ApiOperationMark: Mark[APIOperation] = Mark(attr_name="api_operation")
