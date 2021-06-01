from contextlib import suppress
from functools import lru_cache
from typing import Any, Dict, Generator, List

import requests
from hypothesis.strategies import SearchStrategy

from ...models import APIOperation, Case
from ._hypothesis import PARAMETERS, get_case_strategy
from .constants import LOCATION_TO_CONTAINER


def get_object_example_from_properties(object_schema: Dict[str, Any]) -> Dict[str, Any]:
    return {
        prop_name: prop["example"]
        for prop_name, prop in object_schema.get("properties", {}).items()
        if "example" in prop
    }


@lru_cache()
def load_external_example(url: str) -> bytes:
    """Load examples the `externalValue` keyword."""
    response = requests.get(url)
    response.raise_for_status()
    return response.content


def get_examples(examples: Dict[str, Any]) -> Generator[Any, None, None]:
    for example in examples.values():
        # IDEA: report when it is not a dictionary
        if isinstance(example, dict):
            if "value" in example:
                yield example["value"]
            elif "externalValue" in example:
                with suppress(requests.RequestException):
                    # Report a warning if not available?
                    yield load_external_example(example["externalValue"])


def get_parameter_examples(operation_definition: Dict[str, Any], examples_field: str) -> List[Dict[str, Any]]:
    """Gets parameter examples from OAS3 `examples` keyword or `x-examples` for Swagger 2."""
    return [
        {
            "type": LOCATION_TO_CONTAINER.get(parameter["in"]),
            "name": parameter["name"],
            "examples": list(get_examples(parameter[examples_field])),
        }
        for parameter in operation_definition.get("parameters", [])
        if examples_field in parameter
    ]


def get_parameter_example_from_properties(operation_definition: Dict[str, Any]) -> Dict[str, Any]:
    static_parameters: Dict[str, Any] = {}
    for parameter in operation_definition.get("parameters", []):
        parameter_schema = parameter["schema"] if "schema" in parameter else parameter
        example = get_object_example_from_properties(parameter_schema)
        if example:
            parameter_type = LOCATION_TO_CONTAINER[parameter["in"]]
            if parameter_type != "body":
                if parameter_type not in static_parameters:
                    static_parameters[parameter_type] = {}
                static_parameters[parameter_type][parameter["name"]] = example
            else:
                # swagger 2 body and formData parameters should not include parameter names
                static_parameters[parameter_type] = example
    return static_parameters


def get_request_body_examples(operation_definition: Dict[str, Any], examples_field: str) -> Dict[str, Any]:
    """Gets request body examples from OAS3 `examples` keyword or `x-examples` for Swagger 2."""
    # NOTE. `requestBody` is OAS3-specific. How should it work with OAS2?
    request_bodies_items = operation_definition.get("requestBody", {}).get("content", {}).items()
    if not request_bodies_items:
        return {}
    # first element in tuple is media type, second element is dict
    _, schema = next(iter(request_bodies_items))
    examples = schema.get(examples_field, {})
    return {
        "type": "body",
        "examples": list(get_examples(examples)),
    }


def get_request_body_example_from_properties(operation_definition: Dict[str, Any]) -> Dict[str, Any]:
    static_parameters: Dict[str, Any] = {}
    request_bodies_items = operation_definition.get("requestBody", {}).get("content", {}).items()
    if request_bodies_items:
        _, request_body_schema = next(iter(request_bodies_items))
        example = get_object_example_from_properties(request_body_schema.get("schema", {}))
        if example:
            static_parameters["body"] = example

    return static_parameters


def get_static_parameters_from_example(operation: APIOperation) -> Dict[str, Any]:
    static_parameters = {}
    for name in PARAMETERS:
        parameters = getattr(operation, name)
        example = parameters.example
        if example:
            static_parameters[name] = example
    return static_parameters


def get_static_parameters_from_examples(operation: APIOperation, examples_field: str) -> List[Dict[str, Any]]:
    """Get static parameters from OpenAPI examples keyword."""
    operation_definition = operation.definition.resolved
    return merge_examples(
        get_parameter_examples(operation_definition, examples_field),
        get_request_body_examples(operation_definition, examples_field),
    )


def get_static_parameters_from_properties(operation: APIOperation) -> Dict[str, Any]:
    operation_definition = operation.definition.resolved
    return {
        **get_parameter_example_from_properties(operation_definition),
        **get_request_body_example_from_properties(operation_definition),
    }


def get_strategies_from_examples(
    operation: APIOperation, examples_field: str = "examples"
) -> List[SearchStrategy[Case]]:
    maps = {}
    for location, container in LOCATION_TO_CONTAINER.items():
        serializer = operation.get_parameter_serializer(location)
        if serializer is not None:
            maps[container] = serializer

    def serialize_components(case: Case) -> Case:
        """Applies special serialization rules for case components.

        For example, here, query parameters will be rendered in the `deepObject` style if needed.
        """
        for container, map_func in maps.items():
            value = getattr(case, container)
            setattr(case, container, map_func(value))
        return case

    strategies = [
        get_case_strategy(operation=operation, **static_parameters).map(serialize_components)
        for static_parameters in get_static_parameters_from_examples(operation, examples_field)
        if static_parameters
    ]
    for static_parameters in static_parameters_union(
        get_static_parameters_from_example(operation), get_static_parameters_from_properties(operation)
    ):
        strategies.append(get_case_strategy(operation=operation, **static_parameters).map(serialize_components))
    return strategies


def merge_examples(
    parameter_examples: List[Dict[str, Any]], request_body_examples: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Create list of static parameter objects from the parameter and request body examples."""
    static_parameter_list = []
    for idx in range(num_examples(parameter_examples, request_body_examples)):
        static_parameters: Dict[str, Any] = {}
        for parameter in parameter_examples:
            container = static_parameters.setdefault(parameter["type"], {})
            container[parameter["name"]] = parameter["examples"][min(idx, len(parameter["examples"]) - 1)]
        if "examples" in request_body_examples and request_body_examples["examples"]:
            static_parameters[request_body_examples["type"]] = request_body_examples["examples"][
                min(idx, len(request_body_examples["examples"]) - 1)
            ]
        static_parameter_list.append(static_parameters)
    return static_parameter_list


def static_parameters_union(sp_1: Dict[str, Any], sp_2: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Fill missing parameters in each static parameter dict with parameters provided in the other dict."""
    full_static_parameters = (_static_parameters_union(sp_1, sp_2), _static_parameters_union(sp_2, sp_1))
    return [static_parameter for static_parameter in full_static_parameters if static_parameter]


def _static_parameters_union(base_obj: Dict[str, Any], fill_obj: Dict[str, Any]) -> Dict[str, Any]:
    """Fill base_obj with parameter examples in fill_obj that were not in base_obj."""
    if not base_obj:
        return {}

    full_static_parameters: Dict[str, Any] = {**base_obj}

    for parameter_type, examples in fill_obj.items():
        if parameter_type not in full_static_parameters:
            full_static_parameters[parameter_type] = examples
        elif parameter_type != "body":
            # copy individual parameter names.
            # body is unnamed, single examples, so we only do this for named parameters.
            for parameter_name, example in examples.items():
                if parameter_name not in full_static_parameters[parameter_type]:
                    full_static_parameters[parameter_type][parameter_name] = example
    return full_static_parameters


def num_examples(parameter_examples: List[Dict[str, Any]], request_body_examples: Dict[str, Any]) -> int:
    max_parameter_examples = (
        max(len(parameter["examples"]) for parameter in parameter_examples) if parameter_examples else 0
    )
    num_request_body_examples = len(request_body_examples["examples"]) if "examples" in request_body_examples else 0
    return max(max_parameter_examples, num_request_body_examples)
