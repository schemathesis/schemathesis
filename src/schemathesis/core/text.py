from __future__ import annotations


def to_pascal_case(text: str) -> str:
    # snake_case/kebab-case - split and capitalize each word
    if "_" in text or "-" in text:
        parts = text.replace("-", "_").split("_")
        return "".join(word.capitalize() for word in parts if word)
    # camelCase - just uppercase first letter, preserve the rest
    return text[0].upper() + text[1:] if text else text


def to_snake_case(text: str) -> str:
    text = text.replace("-", "_")
    # Insert underscores before uppercase letters
    result = []
    for i, char in enumerate(text):
        # Add underscore before uppercase (except at start)
        if i > 0 and char.isupper():
            result.append("_")
        result.append(char.lower())
    return "".join(result)
