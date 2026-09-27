from __future__ import annotations

from base64 import b64decode

# Smallest well-formed files of common upload formats, keyed by extension.
FILE_SAMPLES: dict[str, bytes] = {
    "png": b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAAAAAA6fptVAAAACklEQVR4nGNgAAAAAgABSK+kcQAAAABJRU5ErkJggg=="),
    "jpg": b64decode(
        "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAFA3PEY8MlBGQUZaVVBfeMiCeG5uePWvuZHI////////////////////////////////////////"
        "////////////wAALCAABAAEBAREA/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQR"
        "BRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4"
        "eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/9oACAEB"
        "AAA/AKVf/9k="
    ),
    "gif": b64decode("R0lGODdhAQABAIEAAAAAAAAAAAAAAAAAACwAAAAAAQABAAAIBAABBAQAOw=="),
    "webp": b64decode("UklGRhoAAABXRUJQVlA4TA4AAAAvAAAAAAcQEf0PRET/Aw=="),
    "pdf": b64decode(
        "JVBERi0xLjQKMSAwIG9iago8PC9UeXBlL0NhdGFsb2cvUGFnZXMgMiAwIFI+PgplbmRvYmoKMiAwIG9iago8PC9UeXBlL1BhZ2VzL0tpZHNb"
        "MyAwIFJdL0NvdW50IDE+PgplbmRvYmoKMyAwIG9iago8PC9UeXBlL1BhZ2UvUGFyZW50IDIgMCBSL01lZGlhQm94WzAgMCAxIDFdPj4KZW5k"
        "b2JqCnhyZWYKMCA0CjAwMDAwMDAwMDAgNjU1MzUgZiAKMDAwMDAwMDAwOSAwMDAwMCBuIAowMDAwMDAwMDU0IDAwMDAwIG4gCjAwMDAwMDAx"
        "MDUgMDAwMDAgbiAKdHJhaWxlcgo8PC9TaXplIDQvUm9vdCAxIDAgUj4+CnN0YXJ0eHJlZgoxNjYKJSVFT0YK"
    ),
    "zip": b64decode(
        "UEsDBBQAAAAAAFCXO11DvrfoAQAAAAEAAAAFAAAAYS50eHRhUEsBAhQDFAAAAAAAUJc7XUO+t+gBAAAAAQAAAAUAAAAAAAAAAAAAAIABAAAA"
        "AGEudHh0UEsFBgAAAAABAAEAMwAAACQAAAAAAA=="
    ),
}

_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"%PDF-", "pdf"),
    (b"PK\x03\x04", "zip"),
)


def file_extension(data: bytes) -> str | None:
    """Extension of the file format `data` starts with, if it is a known one."""
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    for signature, extension in _SIGNATURES:
        if data.startswith(signature):
            return extension
    return None
