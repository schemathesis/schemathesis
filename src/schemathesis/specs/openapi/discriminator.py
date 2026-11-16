import re
from pathlib import PurePosixPath
from urllib.parse import urlsplit

# Characters the Open API spec allows in component names, such as `my.org.User`.
_SCHEMA_NAME = re.compile(r"[a-zA-Z0-9._-]+")


def get_implicit_discriminator_value(uri: str) -> str:
    # Schema names read as names, as the spec recommends, except schema document names like `cat.json`.
    if _SCHEMA_NAME.fullmatch(uri) and not uri.endswith((".json", ".yaml", ".yml")):
        return uri
    parsed = urlsplit(uri)
    if parsed.fragment:
        return parsed.fragment.rstrip("/").rsplit("/", 1)[-1]
    return PurePosixPath(parsed.path).stem
