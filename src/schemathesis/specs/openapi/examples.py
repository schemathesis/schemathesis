from typing import Any, Dict, List, Optional

from hypothesis.strategies import SearchStrategy

from ..._hypothesis import LOCATION_TO_CONTAINER, PARAMETERS, _get_case_strategy, prepare_strategy
from ...models import Case, Endpoint


def get_param_examples(endpoint_def: Dict[str, Any], examples_field: str) -> List[Dict[str, Any]]:
    return [
        {
            "type": LOCATION_TO_CONTAINER.get(param["in"]),
            "name": param["name"],
            "examples": [example["value"] for example in param[examples_field].values()],
        }
        for param in endpoint_def.get("parameters", [])
        if examples_field in param
    ]


def get_request_body_examples(endpoint_def: Dict[str, Any], examples_field: str) -> Dict[str, Any]:
    request_bodies_items = endpoint_def.get("requestBody", {}).get("content", {}).items()
    if not request_bodies_items:
        return {}
    # first element in tuple in media type, second element is dict
    media_type, schema = next(iter(request_bodies_items))
    param_type = "body" if media_type != "multipart/form-data" else "form_data"
    return {"type": param_type, "examples": [example["value"] for example in schema.get(examples_field, {}).values()]}


def get_static_params_from_example(endpoint: Endpoint) -> Dict[str, Any]:
    static_parameters = {}
    for name in PARAMETERS:
        parameter = getattr(endpoint, name)
        if parameter is not None and "example" in parameter:
            static_parameters[name] = parameter["example"]
    return static_parameters


def get_static_params_from_examples(endpoint: Endpoint, examples_field: str) -> List[Dict[str, Any]]:
    endpoint_def = endpoint.definition.resolved
    return merge_examples(
        get_param_examples(endpoint_def, examples_field), get_request_body_examples(endpoint_def, examples_field)
    )


def get_strategies_from_examples(endpoint: Endpoint, examples_field: str = "examples") -> List[SearchStrategy[Case]]:
    strategies = [
        get_strategy(endpoint, static_params)
        for static_params in get_static_params_from_examples(endpoint, examples_field)
    ]
    strategy = get_strategy(endpoint, get_static_params_from_example(endpoint))
    if strategy is not None:
        strategies.append(strategy)
    return strategies


def get_strategy(endpoint: Endpoint, static_parameters: Dict[str, Any]) -> Optional[SearchStrategy[Case]]:
    if static_parameters:
        strategies = {
            parameter: prepare_strategy(
                parameter, getattr(endpoint, parameter), endpoint.get_hypothesis_conversions(parameter)
            )
            for parameter in PARAMETERS - set(static_parameters)
            if getattr(endpoint, parameter) is not None
        }
        return _get_case_strategy(endpoint, static_parameters, strategies)
    return None


def merge_examples(
    parameter_examples: List[Dict[str, Any]], request_body_examples: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Create list of static parameter objects from parameter and request body examples."""
    static_param_list = []
    for idx in range(num_examples(parameter_examples, request_body_examples)):
        static_params: Dict[str, Any] = {}
        for param in parameter_examples:
            if param["type"] not in static_params:
                static_params[param["type"]] = {}
            static_params[param["type"]][param["name"]] = param["examples"][min(idx, len(param["examples"]) - 1)]
        if "examples" in request_body_examples:
            static_params[request_body_examples["type"]] = request_body_examples["examples"][
                min(idx, len(request_body_examples["examples"]) - 1)
            ]
        static_param_list.append(static_params)
    return static_param_list


def num_examples(parameter_examples: List[Dict[str, Any]], request_body_examples: Dict[str, Any]) -> int:
    max_param_examples = max(len(param["examples"]) for param in parameter_examples) if parameter_examples else 0
    num_request_body_examples = len(request_body_examples["examples"]) if "examples" in request_body_examples else 0
    return max(max_param_examples, num_request_body_examples)
