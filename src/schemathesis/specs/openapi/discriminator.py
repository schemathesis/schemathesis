from pathlib import PurePosixPath
from urllib.parse import urlsplit


def get_implicit_discriminator_value(uri: str) -> str:
    parsed = urlsplit(uri)
    if parsed.fragment:
        return parsed.fragment.rstrip("/").rsplit("/", 1)[-1]
    return PurePosixPath(parsed.path).stem
