from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from tools.corpus.io import CorpusEntry, Loader, json_loads, load_from_corpus, read_corpus_file

CORPUS_SCHEME = "corpus://"


def parse_corpus_path(spec: str) -> tuple[str, str]:
    if not spec.startswith(CORPUS_SCHEME):
        raise ValueError(f"not a corpus:// spec: {spec!r}")
    remainder = spec[len(CORPUS_SCHEME) :]
    corpus_name, _, schema_name = remainder.partition("/")
    if not corpus_name or not schema_name:
        raise ValueError(f"corpus:// path must be 'corpus://CORPUS_NAME/SCHEMA_NAME', got: {spec!r}")
    return corpus_name, schema_name


def load_schema_dict(spec: str) -> CorpusEntry:
    if spec.startswith(CORPUS_SCHEME):
        corpus_name, schema_name = parse_corpus_path(spec)
        with read_corpus_file(corpus_name) as tar:
            schema = load_from_corpus(schema_name, tar)
        return CorpusEntry(corpus=corpus_name, name=schema_name, schema=schema)

    if spec.lower().startswith(("http://", "https://")):
        import requests

        response = requests.get(spec, timeout=30)
        response.raise_for_status()
        return CorpusEntry(corpus="external", name=spec, schema=_decode_text(response.text))

    path = Path(spec).expanduser().resolve()
    return CorpusEntry(corpus="external", name=path.name, schema=_decode_text(path.read_text(), suffix=path.suffix))


def _decode_text(text: str, *, suffix: str | None = None) -> dict[str, Any]:
    if suffix and suffix.lower() in (".yaml", ".yml"):
        return yaml.load(text, Loader)
    try:
        return json_loads(text)
    except Exception:
        return yaml.load(text, Loader)
