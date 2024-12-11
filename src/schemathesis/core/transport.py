from typing import Any

from schemathesis.core.version import SCHEMATHESIS_VERSION

USER_AGENT = f"schemathesis/{SCHEMATHESIS_VERSION}"


def prepare_urlencoded(data: Any) -> Any:
    if isinstance(data, list):
        output = []
        for item in data:
            if isinstance(item, dict):
                for key, value in item.items():
                    output.append((key, value))
            else:
                output.append(item)
        return output
    return data
