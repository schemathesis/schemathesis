from typing import Any, Dict, List

from hypothesis.strategies import SearchStrategy

from ..._hypothesis import LOCATION_TO_CONTAINER, PARAMETERS, _get_case_strategy, prepare_strategy
from ...models import Case, Endpoint


def get_object_example_from_properties(object_schema: Dict[str, Any]) -> Dict[str, Any]:
    return {
        prop_name: prop["example"]
        for prop_name, prop in object_schema.get("properties", {}).items()
        if "example" in prop
    }


def get_parameter_examples(endpoint_def: Dict[str, Any], examples_field: str) -> List[Dict[str, Any]]:
    """Gets parameter examples from OAS3 `examples` keyword or `x-examples` for Swagger 2."""
    return [
        {
            "type": LOCATION_TO_CONTAINER.get(parameter["in"]),
            "name": parameter["name"],
            "examples": [example["value"] for example in parameter[examples_field].values()],
        }
        for parameter in endpoint_def.get("parameters", [])
        if examples_field in parameter
    ]


def get_parameter_example_from_properties(endpoint_def: Dict[str, Any]) -> Dict[str, Any]:
    static_parameters: Dict[str, Any] = {}
    for parameter in endpoint_def.get("parameters", []):
        parameter_schema = parameter["schema"] if "schema" in parameter else parameter
        example = get_object_example_from_properties(parameter_schema)
        if example:
            parameter_type = LOCATION_TO_CONTAINER[parameter["in"]]
            if parameter_type not in ("body", "form_data"):
                if parameter_type not in static_parameters:
                    static_parameters[parameter_type] = {}
                static_parameters[parameter_type][parameter["name"]] = example
            else:
                # swagger 2 body and formData parameters should not include parameter name
                static_parameters[parameter_type] = example
    return static_parameters


def get_request_body_examples(endpoint_def: Dict[str, Any], examples_field: str) -> Dict[str, Any]:
    """Gets request body examples from OAS3 `examples` keyword or `x-examples` for Swagger 2."""
    request_bodies_items = endpoint_def.get("requestBody", {}).get("content", {}).items()
    if not request_bodies_items:
        return {}
    # first element in tuple in media type, second element is dict
    media_type, schema = next(iter(request_bodies_items))
    parameter_type = "body" if media_type != "multipart/form-data" else "form_data"
    return {
        "type": parameter_type,
        "examples": [example["value"] for example in schema.get(examples_field, {}).values()],
    }


def get_request_body_example_from_properties(endpoint_def: Dict[str, Any]) -> Dict[str, Any]:
    static_parameters: Dict[str, Any] = {}
    request_bodies_items = endpoint_def.get("requestBody", {}).get("content", {}).items()
    if request_bodies_items:
        media_type, request_body_schema = next(iter(request_bodies_items))
        example = get_object_example_from_properties(request_body_schema.get("schema", {}))
        if example:
            request_body_type = "body" if media_type != "multipart/form-data" else "form_data"
            static_parameters[request_body_type] = example

    return static_parameters


def get_static_parameters_from_example(endpoint: Endpoint) -> Dict[str, Any]:
    static_parameters = {}
    for name in PARAMETERS:
        parameter = getattr(endpoint, name)
        if parameter is not None and "example" in parameter:
            static_parameters[name] = parameter["example"]
    return static_parameters


def get_static_parameters_from_examples(endpoint: Endpoint, examples_field: str) -> List[Dict[str, Any]]:
    """Get static parameters from OpenAPI examples keyword."""
    endpoint_def = endpoint.definition.resolved
    return merge_examples(
        get_parameter_examples(endpoint_def, examples_field), get_request_body_examples(endpoint_def, examples_field)
    )


def get_static_parameters_from_properties(endpoint: Endpoint) -> Dict[str, Any]:
    endpoint_def = endpoint.definition.resolved
    return {
        **get_parameter_example_from_properties(endpoint_def),
        **get_request_body_example_from_properties(endpoint_def),
    }


def get_strategies_from_examples(endpoint: Endpoint, examples_field: str = "examples") -> List[SearchStrategy[Case]]:
    strategies = [
        get_strategy(endpoint, static_parameters)
        for static_parameters in get_static_parameters_from_examples(endpoint, examples_field)
        if static_parameters
    ]
    for static_parameters in static_parameters_union(
        get_static_parameters_from_example(endpoint), get_static_parameters_from_properties(endpoint)
    ):
        strategies.append(get_strategy(endpoint, static_parameters))
    return strategies


def get_strategy(endpoint: Endpoint, static_parameters: Dict[str, Any]) -> SearchStrategy[Case]:
    strategies = {
        parameter: prepare_strategy(
            parameter, getattr(endpoint, parameter), endpoint.get_hypothesis_conversions(parameter)
        )
        for parameter in PARAMETERS - set(static_parameters)
        if getattr(endpoint, parameter) is not None
    }
    return _get_case_strategy(endpoint, static_parameters, strategies)


def merge_examples(
    parameter_examples: List[Dict[str, Any]], request_body_examples: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Create list of static parameter objects from parameter and request body examples."""
    static_parameter_list = []
    for idx in range(num_examples(parameter_examples, request_body_examples)):
        static_parameters: Dict[str, Any] = {}
        for parameter in parameter_examples:
            if parameter["type"] not in static_parameters:
                static_parameters[parameter["type"]] = {}
            static_parameters[parameter["type"]][parameter["name"]] = parameter["examples"][
                min(idx, len(parameter["examples"]) - 1)
            ]
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
        elif parameter_type not in ("body", "form_data"):
            # copy individual parameter names.
            # body and form_data are unnamed, single examples, so we only do this for named parameters.
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
