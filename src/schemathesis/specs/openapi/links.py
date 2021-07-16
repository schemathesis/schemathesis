"""Open API links support.

Based on https://swagger.io/docs/specification/links/
"""
from copy import deepcopy
from difflib import get_close_matches
from typing import Any, Dict, Generator, List, NoReturn, Optional, Sequence, Tuple, Union

import attr

from ...models import APIOperation, Case
from ...parameters import ParameterSet
from ...stateful import Direction, ParsedData, StatefulTest
from ...types import NotSet
from ...utils import NOT_SET, GenericResponse
from . import expressions
from .constants import LOCATION_TO_CONTAINER
from .parameters import OpenAPI20Body, OpenAPI30Body, OpenAPIParameter


@attr.s(slots=True, repr=False)  # pragma: no mutate
class Link(StatefulTest):
    operation: APIOperation = attr.ib()  # pragma: no mutate
    parameters: Dict[str, Any] = attr.ib()  # pragma: no mutate
    request_body: Any = attr.ib(default=NOT_SET)  # pragma: no mutate

    @request_body.validator
    def is_defined(self, attribute: attr.Attribute, value: Any) -> None:
        if value is not NOT_SET and not self.operation.body:
            # Link defines `requestBody` for a parameter that does not accept one
            raise ValueError(
                f"Request body is not defined in API operation {self.operation.method.upper()} {self.operation.path}"
            )

    @classmethod
    def from_definition(
        cls, name: str, definition: Dict[str, Dict[str, Any]], source_operation: APIOperation
    ) -> "Link":
        # Links can be behind a reference
        _, definition = source_operation.schema.resolver.resolve_in_scope(  # type: ignore
            definition, source_operation.definition.scope
        )
        if "operationId" in definition:
            # source_operation.schema is `BaseOpenAPISchema` and has this method
            operation = source_operation.schema.get_operation_by_id(definition["operationId"])  # type: ignore
        else:
            operation = source_operation.schema.get_operation_by_reference(definition["operationRef"])  # type: ignore
        return cls(
            # Pylint can't detect that the API operation is always defined at this point
            # E.g. if there is no matching operation or no operations at all, then a ValueError will be risen
            name=name,
            operation=operation,  # pylint: disable=undefined-loop-variable
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

    def make_operation(self, collected: List[ParsedData]) -> APIOperation:
        """Create a modified version of the original API operation with additional data merged in."""
        # We split the gathered data among all locations & store the original parameter
        containers = {
            location: {
                parameter.name: {"options": [], "parameter": parameter}
                for parameter in getattr(self.operation, container_name)
            }
            for location, container_name in LOCATION_TO_CONTAINER.items()
        }
        # There might be duplicates in the data
        for item in set(collected):
            for name, value in item.parameters.items():
                container = self._get_container_by_parameter_name(name, containers)
                container.append(value)
            if "body" in containers["body"] and item.body is not NOT_SET:
                containers["body"]["body"]["options"].append(item.body)
        # These are the final `path_parameters`, `query`, and other API operation components
        components: Dict[str, ParameterSet] = {
            container_name: getattr(self.operation, container_name).__class__()
            for location, container_name in LOCATION_TO_CONTAINER.items()
        }
        # Here are all components that are filled with parameters
        for location, parameters in containers.items():
            for name, parameter_data in parameters.items():
                parameter = parameter_data["parameter"]
                if parameter_data["options"]:
                    definition = deepcopy(parameter.definition)
                    if "schema" in definition:
                        # The actual schema doesn't matter since we have a list of allowed values
                        definition["schema"] = {"enum": parameter_data["options"]}
                    else:
                        # Other schema-related keywords will be ignored later, during the canonicalisation step
                        # inside `hypothesis-jsonschema`
                        definition["enum"] = parameter_data["options"]
                    new_parameter: OpenAPIParameter
                    if isinstance(parameter, OpenAPI30Body):
                        new_parameter = parameter.__class__(
                            definition, media_type=parameter.media_type, required=parameter.required
                        )
                    elif isinstance(parameter, OpenAPI20Body):
                        new_parameter = parameter.__class__(definition, media_type=parameter.media_type)
                    else:
                        new_parameter = parameter.__class__(definition)
                    components[LOCATION_TO_CONTAINER[location]].add(new_parameter)
                else:
                    # No options were gathered for this parameter - use the original one
                    components[LOCATION_TO_CONTAINER[location]].add(parameter)
        return self.operation.clone(**components)

    def _get_container_by_parameter_name(self, full_name: str, templates: Dict[str, Dict[str, Dict[str, Any]]]) -> List:
        """Detect in what request part the parameters is defined."""
        location: Optional[str]
        try:
            # The parameter name is prefixed with its location. Example: `path.id`
            location, name = full_name.split(".")
        except ValueError:
            location, name = None, full_name
        if location:
            try:
                parameters = templates[location]
            except KeyError:
                self._unknown_parameter(full_name)
        else:
            for parameters in templates.values():
                if name in parameters:
                    break
            else:
                self._unknown_parameter(full_name)
        if not parameters:
            self._unknown_parameter(full_name)
        return parameters[name]["options"]

    def _unknown_parameter(self, name: str) -> NoReturn:
        raise ValueError(
            f"Parameter `{name}` is not defined in API operation {self.operation.method.upper()} {self.operation.path}"
        )


def get_links(response: GenericResponse, operation: APIOperation, field: str) -> Sequence[Link]:
    """Get `x-links` / `links` definitions from the schema."""
    responses = operation.definition.resolved["responses"]
    if str(response.status_code) in responses:
        response_definition = responses[str(response.status_code)]
    elif response.status_code in responses:
        response_definition = responses[response.status_code]
    else:
        response_definition = responses.get("default", {})
    links = response_definition.get(field, {})
    return [Link.from_definition(name, definition, operation) for name, definition in links.items()]


@attr.s(slots=True, repr=False)  # pragma: no mutate
class OpenAPILink(Direction):
    """Alternative approach to link processing.

    NOTE. This class will replace `Link` in the future.
    """

    name: str = attr.ib()  # pragma: no mutate
    status_code: str = attr.ib()  # pragma: no mutate
    definition: Dict[str, Any] = attr.ib()  # pragma: no mutate
    operation: APIOperation = attr.ib()  # pragma: no mutate
    parameters: List[Tuple[Optional[str], str, str]] = attr.ib(init=False)  # pragma: no mutate
    body: Union[Dict[str, Any], NotSet] = attr.ib(init=False)  # pragma: no mutate

    def __attrs_post_init__(self) -> None:
        self.parameters = [
            normalize_parameter(parameter, expression)
            for parameter, expression in self.definition.get("parameters", {}).items()
        ]
        self.body = self.definition.get("requestBody", NOT_SET)

    def set_data(self, case: Case, **kwargs: Any) -> None:
        """Assign all linked definitions to the new case instance."""
        context = kwargs["context"]
        self.set_parameters(case, context)
        self.set_body(case, context)
        case.set_source(context.response, context.case)

    def set_parameters(self, case: Case, context: expressions.ExpressionContext) -> None:
        for location, name, expression in self.parameters:
            container = get_container(case, location, name)
            # Might happen if there is directly specified container,
            # but the schema has no parameters of such type at all.
            # Therefore the container is empty, otherwise it will be at least an empty object
            if container is None:
                message = f"No such parameter in `{case.operation.method.upper()} {case.operation.path}`: `{name}`."
                possibilities = [param.name for param in case.operation.definition.parameters]
                matches = get_close_matches(name, possibilities)
                if matches:
                    message += f" Did you mean `{matches[0]}`?"
                raise ValueError(message)
            container[name] = expressions.evaluate(expression, context)

    def set_body(self, case: Case, context: expressions.ExpressionContext) -> None:
        if self.body is not NOT_SET:
            case.body = expressions.evaluate(self.body, context)

    def get_target_operation(self) -> APIOperation:
        if "operationId" in self.definition:
            return self.operation.schema.get_operation_by_id(self.definition["operationId"])  # type: ignore
        return self.operation.schema.get_operation_by_reference(self.definition["operationRef"])  # type: ignore


def get_container(case: Case, location: Optional[str], name: str) -> Optional[Dict[str, Any]]:
    """Get a container that suppose to store the given parameter."""
    if location:
        container_name = LOCATION_TO_CONTAINER[location]
    else:
        for param in case.operation.definition.parameters:
            if param.name == name:
                container_name = LOCATION_TO_CONTAINER[param.location]
                break
        else:
            raise ValueError(f"Parameter `{name}` is not defined in API operation `{case.operation.verbose_name}`")
    return getattr(case, container_name)


def normalize_parameter(parameter: str, expression: str) -> Tuple[Optional[str], str, str]:
    """Normalize runtime expressions.

    Runtime expressions may have parameter names prefixed with their location - `path.id`.
    At the same time, parameters could be defined without a prefix - `id`.
    We need to normalize all parameters to the same form to simplify working with them.
    """
    try:
        # The parameter name is prefixed with its location. Example: `path.id`
        location, name = tuple(parameter.split("."))
        return location, name, expression
    except ValueError:
        return None, parameter, expression


def get_all_links(operation: APIOperation) -> Generator[Tuple[str, OpenAPILink], None, None]:
    for status_code, definition in operation.definition.resolved["responses"].items():
        for name, link_definition in definition.get(operation.schema.links_field, {}).items():  # type: ignore
            yield status_code, OpenAPILink(name, status_code, link_definition, operation)


StatusCode = Union[str, int]


def _get_response_by_status_code(responses: Dict[StatusCode, Dict[str, Any]], status_code: Union[str, int]) -> Dict:
    if isinstance(status_code, int):
        # Invalid schemas may contain status codes as integers
        if status_code in responses:
            return responses[status_code]
        # Passed here as an integer, but there is no such status code as int
        # We cast it to a string because it is either there already and we'll get relevant responses, otherwise
        # a new dict will be created because there is no such status code in the schema (as an int or a string)
        return responses.setdefault(str(status_code), {})
    if status_code.isnumeric():
        # Invalid schema but the status code is passed as a string
        numeric_status_code = int(status_code)
        if numeric_status_code in responses:
            return responses[numeric_status_code]
    # All status codes as strings, including `default` and patterned values like `5XX`
    return responses.setdefault(status_code, {})


def add_link(
    responses: Dict[StatusCode, Dict[str, Any]],
    links_field: str,
    parameters: Optional[Dict[str, str]],
    request_body: Any,
    status_code: StatusCode,
    target: Union[str, APIOperation],
) -> None:
    response = _get_response_by_status_code(responses, status_code)
    links_definition = response.setdefault(links_field, {})
    new_link: Dict[str, Union[str, Dict[str, str]]] = {}
    if parameters is not None:
        new_link["parameters"] = parameters
    if request_body is not None:
        new_link["requestBody"] = request_body
    if isinstance(target, str):
        name = target
        new_link["operationRef"] = target
    else:
        name = f"{target.method.upper()} {target.path}"
        # operationId is a dict lookup which is more efficient than using `operationRef`, since it
        # doesn't involve reference resolving when we will look up for this target during testing.
        if "operationId" in target.definition.resolved:
            new_link["operationId"] = target.definition.resolved["operationId"]
        else:
            new_link["operationRef"] = target.operation_reference
    # The name is arbitrary, so we don't really case what it is,
    # but it should not override existing links
    while name in links_definition:
        name += "_new"
    links_definition[name] = new_link
