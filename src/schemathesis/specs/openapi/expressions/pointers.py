from typing import Any, Dict, List, Optional, Union


def resolve(document: Any, pointer: str) -> Optional[Union[Dict, List, str, int, float]]:
    """Implementation is adapted from Rust's `serde-json` crate.

    Ref: https://github.com/serde-rs/json/blob/master/src/value/mod.rs#L751
    """
    if not pointer:
        return document
    if not pointer.startswith("/"):
        return None

    def replace(value: str) -> str:
        return value.replace("~1", "/").replace("~0", "~")

    tokens = map(replace, pointer.split("/")[1:])
    target = document
    for token in tokens:
        if isinstance(target, dict):
            target = target.get(token)
        elif isinstance(target, list):
            try:
                target = target[int(token)]
            except IndexError:
                return None
        else:
            return None
    return target
