"""Open API links support.

Based on https://swagger.io/docs/specification/links/
"""
from copy import deepcopy
from typing import Any, Dict, List, NoReturn, Optional, Sequence, Tuple

import attr

from ..._hypothesis import LOCATION_TO_CONTAINER
from ...models import Case, Endpoint
from ...stateful import ParsedData, StatefulTest
from ...utils import NOT_SET, GenericResponse
from . import expressions


@attr.s(slots=True)  # pragma: no mutate
class Link(StatefulTest):
    endpoint: Endpoint = attr.ib()  # pragma: no mutate
    parameters: Dict[str, Any] = attr.ib()  # pragma: no mutate
    request_body: Any = attr.ib(default=NOT_SET)  # pragma: no mutate

    @classmethod
    def from_definition(cls, name: str, definition: Dict[str, Dict[str, Any]], source_endpoint: Endpoint) -> "Link":
        # Links can be behind a reference
        _, definition = source_endpoint.schema.resolver.resolve_in_scope(  # type: ignore
            definition, source_endpoint.definition.scope
        )
        if "operationId" in definition:
            # source_endpoint.schema is `BaseOpenAPISchema` and has this method
            endpoint = source_endpoint.schema.get_endpoint_by_operation_id(definition["operationId"])  # type: ignore
        else:
            endpoint = source_endpoint.schema.get_endpoint_by_reference(definition["operationRef"])  # type: ignore
        return cls(
            # Pylint can't detect that endpoint is always defined at this point
            # E.g. if there is no matching endpoint or no endpoints at all, then a ValueError will be risen
            name=name,
            endpoint=endpoint,  # pylint: disable=undefined-loop-variable
            parameters=definition.get("parameters", {}),
            request_body=definition.get("requestBody", NOT_SET),  # `None` might be a valid value - `null`
        )

    def parse(self, case: Case, response: GenericResponse) -> ParsedData:
        """Parse data into a structure expected by links definition."""
        context = expressions.ExpressionContext(case=case, response=response)
        parameters = {
            parameter: expressions.evaluate(expression, context) for parameter, expression in self.parameters.items()
        }
        return ParsedData(
            parameters=parameters,
            # https://github.com/OAI/OpenAPI-Specification/blob/master/versions/3.0.3.md#link-object
            # > A literal value or {expression} to use as a request body when calling the target operation.
            # In this case all literals will be passed as is, and expressions will be evaluated
            body=expressions.evaluate(self.request_body, context),
        )

    def make_endpoint(self, data: List[ParsedData]) -> Endpoint:
        """Create a modified version of the original endpoint with additional data merged in."""
        # For each response from the previous endpoint run we create a new JSON schema and collect all results
        # Instead of unconditionally creating `oneOf` list we gather it separately to optimize
        # the resulting schema later
        variants: Dict[str, List[Dict[str, Any]]] = {
            "path_parameters": [],
            "query": [],
            "headers": [],
            "cookies": [],
            "body": [],
        }

        def make_variants(_item: ParsedData, _templates: Dict[str, Optional[Dict[str, Any]]]) -> None:
            """Add each item from the previous test run to the proper place in the schema.

            Example:
                 If there is `user_id` parameter defined in the query, and we have `{"user_id": 1}` as data, then
                 `1` will be added as a constant in a schema that is responsible for query generation

            """
            for name, value in _item.parameters.items():
                name, container = self._get_container_by_parameter_name(name, _templates)
                container["properties"][name]["const"] = value
            if _item.body is not NOT_SET:
                variants["body"].append({"const": _item.body})

        def add_variants(_templates: Dict[str, Optional[Dict[str, Any]]]) -> None:
            """Add all created schemas as variants to their locations."""
            for location, container_name in LOCATION_TO_CONTAINER.items():
                variant = _templates[location]
                # There could be no schema defined for e.g. `query`, then the container in the `Endpoint` instance
                # is `None`. The resulting
                if variant is not None:
                    variants[container_name].append(variant)

        # It is possible that on the previous test level we gathered non-unique data samples, we can remove duplicates
        # It might happen because of how shrinking works, some examples are retried
        for item in set(data):
            # Copies of the original schemas are templates that could be extended from the data gathered in the
            # previous endpoint test
            templates: Dict[str, Optional[Dict[str, Any]]] = {
                location: deepcopy(getattr(self.endpoint, container_name))
                for location, container_name in LOCATION_TO_CONTAINER.items()
            }
            make_variants(item, templates)
            add_variants(templates)
        components = self._convert_to_schema(variants)
        return self.endpoint.__class__(
            path=self.endpoint.path,
            method=self.endpoint.method,
            definition=self.endpoint.definition,
            schema=self.endpoint.schema,
            app=self.endpoint.app,
            base_url=self.endpoint.base_url,
            path_parameters=components["path_parameters"],
            query=components["query"],
            headers=components["headers"],
            cookies=components["cookies"],
            body=components["body"],
            form_data=self.endpoint.form_data,
        )

    def _get_container_by_parameter_name(
        self, full_name: str, templates: Dict[str, Optional[Dict[str, Any]]]
    ) -> Tuple[str, Dict[str, Any]]:
        """Detect in what request part the parameters is defined."""
        location: Optional[str]
        try:
            # The parameter name is prefixed with its location. Example: `path.id`
            location, name = full_name.split(".")
        except ValueError:
            location, name = None, full_name
        if location:
            try:
                schema = templates[location]
            except KeyError:
                self._unknown_parameter(full_name)
        else:
            for schema in templates.values():
                if schema is not None and name in schema["properties"]:
                    break
            else:
                self._unknown_parameter(full_name)
        if schema is None:
            self._unknown_parameter(full_name)
        return name, schema

    def _convert_to_schema(self, variants: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
        """Convert gathered schema variants to a JSON schema.

        No variants gathered - the original schema is used
        One variant - it is used directly
        Many variants - they are combined via `anyOf`
        """

        def convert(name: str, component: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
            if len(component) == 0:
                return getattr(self.endpoint, name)
            if len(component) == 1:
                return component[0]
            return {"anyOf": component}

        return {name: convert(name, component) for name, component in variants.items()}

    def _unknown_parameter(self, name: str) -> NoReturn:
        raise ValueError(f"Parameter `{name}` is not defined in endpoint {self.endpoint.method} {self.endpoint.path}")


def get_links(response: GenericResponse, endpoint: Endpoint, field: str) -> Sequence[Link]:
    """Get `x-links` / `links` definitions from the schema."""
    responses = endpoint.definition.raw["responses"]
    if str(response.status_code) in responses:
        response_definition = responses[str(response.status_code)]
    else:
        response_definition = responses.get("default", {})
    links = response_definition.get(field, {})
    return [Link.from_definition(name, definition, endpoint) for name, definition in links.items()]
