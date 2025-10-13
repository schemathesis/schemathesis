from __future__ import annotations


def from_parameter(parameter: str, path: str) -> str | None:
    # TODO: support other naming patterns
    # Named like "userId" -> look for "User" resource
    if parameter.endswith("Id"):
        return to_pascal_case(parameter[:-2])
    # Named like "user_id" -> look for "User" resource
    elif parameter.endswith("_id"):
        return to_pascal_case(parameter[:-3])
    # Just "id" -> infer from path context
    elif parameter == "id":
        return from_path(path)
    return None


def from_path(path: str) -> str | None:
    segments = [s for s in path.split("/") if s and "{" not in s]

    if not segments:
        # API Root
        return None

    singular = to_singular(segments[-1])
    return to_pascal_case(singular)


IRREGULAR_TO_PLURAL = {
    "echo": "echoes",
    "dingo": "dingoes",
    "volcano": "volcanoes",
    "tornado": "tornadoes",
    "torpedo": "torpedoes",
    "genus": "genera",
    "viscus": "viscera",
    "stigma": "stigmata",
    "stoma": "stomata",
    "dogma": "dogmata",
    "lemma": "lemmata",
    "anathema": "anathemata",
    "ox": "oxen",
    "axe": "axes",
    "die": "dice",
    "yes": "yeses",
    "foot": "feet",
    "eave": "eaves",
    "goose": "geese",
    "tooth": "teeth",
    "quiz": "quizzes",
    "human": "humans",
    "proof": "proofs",
    "carve": "carves",
    "valve": "valves",
    "looey": "looies",
    "thief": "thieves",
    "groove": "grooves",
    "pickaxe": "pickaxes",
    "passerby": "passersby",
    "canvas": "canvases",
    "use": "uses",
    "case": "cases",
    "vase": "vases",
    "house": "houses",
    "mouse": "mice",
    "reuse": "reuses",
    "abuse": "abuses",
    "excuse": "excuses",
    "cause": "causes",
    "pause": "pauses",
    "base": "bases",
    "phase": "phases",
    "rose": "roses",
    "dose": "doses",
    "nose": "noses",
    "horse": "horses",
    "course": "courses",
    "purpose": "purposes",
    "response": "responses",
    "sense": "senses",
    "tense": "tenses",
    "expense": "expenses",
    "license": "licenses",
    "defense": "defenses",
}
IRREGULAR_TO_SINGULAR = {v: k for k, v in IRREGULAR_TO_PLURAL.items()}
UNCOUNTABLE = frozenset(
    [
        "adulthood",
        "advice",
        "agenda",
        "aid",
        "aircraft",
        "alcohol",
        "ammo",
        "analytics",
        "anime",
        "athletics",
        "audio",
        "bison",
        "blood",
        "bream",
        "buffalo",
        "butter",
        "carp",
        "cash",
        "chassis",
        "chess",
        "clothing",
        "cod",
        "commerce",
        "cooperation",
        "corps",
        "debris",
        "diabetes",
        "digestion",
        "elk",
        "energy",
        "equipment",
        "excretion",
        "expertise",
        "firmware",
        "flounder",
        "fun",
        "gallows",
        "garbage",
        "graffiti",
        "hardware",
        "headquarters",
        "health",
        "herpes",
        "highjinks",
        "homework",
        "housework",
        "information",
        "jeans",
        "justice",
        "kudos",
        "labour",
        "literature",
        "machinery",
        "mackerel",
        "mail",
        "media",
        "mews",
        "moose",
        "music",
        "mud",
        "manga",
        "news",
        "only",
        "personnel",
        "pike",
        "plankton",
        "pliers",
        "police",
        "pollution",
        "premises",
        "rain",
        "research",
        "rice",
        "salmon",
        "scissors",
        "series",
        "sewage",
        "shambles",
        "shrimp",
        "software",
        "staff",
        "swine",
        "tennis",
        "traffic",
        "transportation",
        "trout",
        "tuna",
        "wealth",
        "welfare",
        "whiting",
        "wildebeest",
        "wildlife",
        "you",
        "sheep",
        "deer",
        "species",
        "series",
        "means",
    ]
)


def to_singular(word: str) -> str:
    if word in UNCOUNTABLE:
        return word
    known = IRREGULAR_TO_SINGULAR.get(word)
    if known is not None:
        return known
    if word.endswith("ies") and len(word) > 3 and word[-4] not in "aeiou":
        return word[:-3] + "y"
    if word.endswith("sses"):
        return word[:-2]
    if word.endswith(("xes", "zes", "ches", "shes")):
        return word[:-2]
    # Handle "ses" ending: check if it was "se" + "s" or "s" + "es"
    if word.endswith("ses") and len(word) > 3:
        # "gases" has 's' at position -3, formed from "gas" + "es"
        # "statuses" has 's' at position -3, formed from "status" + "es"
        return word[:-2]
    if word.endswith("s"):
        return word[:-1]
    return word


def to_plural(word: str) -> str:
    if word in UNCOUNTABLE:
        return word
    known = IRREGULAR_TO_PLURAL.get(word)
    if known is not None:
        return known
    # Only change y -> ies after consonants (party -> parties, not day -> days)
    if word.endswith("y") and len(word) > 1 and word[-2] not in "aeiou":
        return word[:-1] + "ies"
    # class -> classes
    if word.endswith("ss"):
        return word + "es"
    # words that normally take -es: box -> boxes
    if word.endswith(("s", "x", "z", "ch", "sh")):
        return word + "es"
    # just add 's' (car -> cars)
    return word + "s"


def to_pascal_case(text: str) -> str:
    parts = text.replace("-", "_").split("_")
    return "".join(word.capitalize() for word in parts if word)


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


def find_matching_field(*, parameter: str, resource: str, fields: list[str]) -> str | None:
    """Find which resource field matches the parameter name."""
    if not fields:
        return None

    # Exact match
    if parameter in fields:
        return parameter

    # Normalize for fuzzy matching
    parameter_normalized = _normalize_for_matching(parameter)
    resource_normalized = _normalize_for_matching(resource)

    # Normalized exact match
    # `brandId` -> `Brand.BrandId`
    for field in fields:
        if _normalize_for_matching(field) == parameter_normalized:
            return field

    # Extract parameter components
    parameter_prefix, param_suffix = _split_parameter_name(parameter)
    parameter_prefix_normalized = _normalize_for_matching(parameter_prefix)

    # Parameter has resource prefix, field might not
    # Example: `channelId` - `Channel.id`
    if parameter_prefix and parameter_prefix_normalized == resource_normalized:
        suffix_normalized = _normalize_for_matching(param_suffix)

        for field in fields:
            field_normalized = _normalize_for_matching(field)
            if field_normalized == suffix_normalized:
                return field

    # Parameter has no prefix, field might have resource prefix
    # Example: `id` - `Channel.channelId`
    if not parameter_prefix and param_suffix:
        expected_field_normalized = resource_normalized + _normalize_for_matching(param_suffix)

        for field in fields:
            field_normalized = _normalize_for_matching(field)
            if field_normalized == expected_field_normalized:
                return field

    return None


def _normalize_for_matching(text: str) -> str:
    """Normalize text for case-insensitive, separator-insensitive matching.

    Examples:
        "channelId" -> "channelid"
        "channel_id" -> "channelid"
        "ChannelId" -> "channelid"
        "Channel" -> "channel"

    """
    return text.lower().replace("_", "").replace("-", "")


def _split_parameter_name(param_name: str) -> tuple[str, str]:
    """Split parameter into (prefix, suffix) components.

    Examples:
        "channelId" -> ("channel", "Id")
        "userId" -> ("user", "Id")
        "user_id" -> ("user", "_id")
        "id" -> ("", "id")
        "channel_id" -> ("channel", "_id")

    """
    if param_name.endswith("Id") and len(param_name) > 2:
        return (param_name[:-2], "Id")

    if param_name.endswith("_id") and len(param_name) > 3:
        return (param_name[:-3], "_id")

    return ("", param_name)


def strip_affixes(name: str, prefixes: list[str], suffixes: list[str]) -> str:
    """Remove common prefixes and suffixes from a name (case-insensitive)."""
    result = name.strip()
    name_lower = result.lower()

    # Remove one matching prefix
    for prefix in prefixes:
        if name_lower.startswith(prefix):
            result = result[len(prefix) :]
            break

    # Remove one matching suffix
    for suffix in suffixes:
        if name_lower.endswith(suffix):
            result = result[: -len(suffix)]
            break

    return result.strip()
