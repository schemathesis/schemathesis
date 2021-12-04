from typing import Any, Dict, List, Tuple, Union

from ...constants import HTTP_METHODS


def is_pattern_error(exception: TypeError) -> bool:
    """Detect whether the input exception was caused by invalid type passed to `re.search`."""
    # This is intentionally simplistic and do not involve any traceback analysis
    return str(exception) == "expected string or bytes-like object"


def find_numeric_http_status_codes(schema: Dict[str, Any]) -> List[Tuple[int, List[Union[str, int]]]]:
    if not isinstance(schema, dict):
        return []
    found = []
    for path, methods in schema.get("paths", {}).items():
        if isinstance(methods, dict):
            for method, definition in methods.items():
                if method not in HTTP_METHODS or not isinstance(definition, dict):
                    continue
                for key in definition.get("responses", {}):
                    if isinstance(key, int):
                        found.append((key, [path, method]))
    return found
