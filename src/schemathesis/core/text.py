from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=2048)
def to_pascal_case(text: str) -> str:
    if "_" in text or "-" in text:
        parts = text.replace("-", "_").split("_")
        return "".join(word.capitalize() for word in parts if word)
    return text[0].upper() + text[1:] if text else text


@lru_cache(maxsize=2048)
def to_snake_case(text: str) -> str:
    text = text.replace("-", "_")
    result = []
    for i, char in enumerate(text):
        if i > 0 and char.isupper():
            result.append("_")
        result.append(char.lower())
    return "".join(result)
