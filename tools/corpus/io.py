from __future__ import annotations

import io
import os
import pathlib
import re
import tarfile
from collections.abc import Generator
from dataclasses import dataclass
from typing import Any

import yaml

try:
    from yaml import CSafeLoader as SafeLoader
except ImportError:
    from yaml import SafeLoader  # type: ignore[assignment]

try:
    import orjson
    from orjson import loads as json_loads

    def json_dumps(obj: Any) -> bytes:
        return orjson.dumps(obj)

except ImportError:
    import json
    from json import loads as json_loads

    def json_dumps(obj: Any) -> bytes:
        return json.dumps(obj, separators=(",", ":")).encode("utf-8")


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "corpus" / "data"
CATALOG_DIR = REPO_ROOT / "test-corpus" / "openapi-directory" / "APIs"

Loader = type("YAMLLoader", (SafeLoader,), {})
Loader.yaml_implicit_resolvers = {  # type: ignore[attr-defined]
    key: [(tag, regexp) for tag, regexp in mapping if tag != "tag:yaml.org,2002:timestamp"]
    for key, mapping in Loader.yaml_implicit_resolvers.copy().items()  # type: ignore[attr-defined]
}

Loader.add_implicit_resolver(  # type: ignore[attr-defined]
    "tag:yaml.org,2002:float",
    re.compile(
        r"""^(?:[-+]?(?:[0-9][0-9_]*)\.[0-9_]*(?:[eE][-+]?[0-9]+)?
                    |[-+]?(?:[0-9][0-9_]*)(?:[eE][-+]?[0-9]+)
                    |\.[0-9_]+(?:[eE][-+]?[0-9]+)?
                    |[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+\.[0-9_]*
                    |[-+]?\.(?:inf|Inf|INF)
                    |\.(?:nan|NaN|NAN))$""",
        re.VERBOSE,
    ),
    list("-+0123456789."),
)


def construct_mapping(self: SafeLoader, node: yaml.Node, deep: bool = False) -> dict[str, Any]:
    if isinstance(node, yaml.MappingNode):
        self.flatten_mapping(node)
    mapping = {}
    for key_node, value_node in node.value:
        if key_node.tag != "tag:yaml.org,2002:str":
            key = key_node.value
        else:
            key = self.construct_object(key_node, deep)
        mapping[key] = self.construct_object(value_node, deep)
    return mapping


Loader.construct_mapping = construct_mapping  # type: ignore[attr-defined]


def create_tar_gz(schemas: dict[str, dict[str, Any]], output_dir: pathlib.Path) -> None:
    """Create compressed API schemas corpus."""
    os.makedirs(output_dir, exist_ok=True)

    for version, version_schemas in schemas.items():
        if version.startswith("2."):
            output_path = output_dir / f"swagger-{version}.tar.gz"
        else:
            output_path = output_dir / f"openapi-{version}.tar.gz"

        with tarfile.open(output_path, "w:gz") as tar_gz:
            for schema_name, schema in version_schemas.items():
                json_path = f"{schema_name}.json"
                json_data = json_dumps(schema)
                info = tarfile.TarInfo(name=json_path)
                info.size = len(json_data)
                tar_gz.addfile(info, io.BytesIO(json_data))


def parse_schemas(directory: pathlib.Path) -> dict[str, dict[str, Any]]:
    schemas: dict[str, dict[str, Any]] = {}

    for root, _, files in os.walk(directory):
        for file in files:
            if file.endswith("swagger.yaml") or file.endswith("openapi.yaml"):
                file_path = os.path.join(root, file)
                try:
                    with open(file_path) as fd:
                        schema = yaml.load(fd, Loader)
                    version = get_schema_version(schema)
                    schema_name = (
                        os.path.relpath(file_path, directory)
                        .replace(os.sep, "/")
                        .replace("/swagger.yaml", "")
                        .replace("/openapi.yaml", "")
                    )
                    if version not in schemas:
                        schemas[version] = {}
                    schemas[version][schema_name] = schema
                except (yaml.YAMLError, KeyError):
                    print(f"Error parsing {file_path}")  # noqa: T201

    return schemas


def get_schema_version(schema: dict[str, Any]) -> str:
    """Extract the schema version from the parsed schema."""
    if "openapi" in schema:
        return schema["openapi"][:3]
    elif "swagger" in schema:
        return schema["swagger"]
    else:
        raise ValueError("Invalid schema format")


def load_from_corpus(file_name: str, corpus: tarfile.TarFile | str) -> dict[str, Any]:
    if isinstance(corpus, str):
        corpus = read_corpus_file(corpus)
    extracted = corpus.extractfile(file_name)
    if extracted is not None:
        return json_loads(extracted.read())
    raise FileNotFoundError(file_name)


def read_corpus_file(name: str, *, data_dir: pathlib.Path = DATA_DIR) -> tarfile.TarFile:
    return tarfile.open(data_dir / f"{name}.tar.gz", "r:gz")


def iter_corpus_file(
    name: str, *, data_dir: pathlib.Path = DATA_DIR
) -> Generator[tuple[str, dict[str, Any]], None, None]:
    """Iterate over the corpus file."""
    with read_corpus_file(name, data_dir=data_dir) as archive:
        for member in archive.getmembers():
            extracted = archive.extractfile(member)
            if extracted is not None:
                yield member.name, json_loads(extracted.read())


def iter_all_corpus_files(
    *, data_dir: pathlib.Path = DATA_DIR
) -> Generator[tuple[str, str, dict[str, Any]], None, None]:
    """Iterate over all corpus files (sorted by archive name for determinism)."""
    for corpus_path in sorted(data_dir.glob("*.tar.gz")):
        corpus_name = corpus_path.name.removesuffix(".tar.gz")
        for file_name, schema in iter_corpus_file(corpus_name, data_dir=data_dir):
            yield corpus_name, file_name, schema


CORPUS_NAMES = ("openapi-3.0", "openapi-3.1", "swagger-2.0")


@dataclass(slots=True, frozen=True)
class CorpusEntry:
    """A single resolved schema, whether it came from a corpus tarball or elsewhere."""

    corpus: str
    name: str
    schema: dict[str, Any]

    @property
    def api(self) -> str:
        return self.name.removesuffix(".json")


def iter_corpus_refs(
    corpus: str | None = None,
    *,
    only: str | None = None,
    limit: int | None = None,
    data_dir: pathlib.Path = DATA_DIR,
) -> Generator[tuple[str, str], None, None]:
    """Stream `(corpus_name, member_name)` pairs from one or all corpus tarballs.

    Same filter semantics as `iter_corpus_streaming` but skips JSON decoding —
    useful when the consumer wants to ship work to subprocesses by reference
    and load schemas independently.
    """
    corpora = [corpus] if corpus else list(CORPUS_NAMES)
    for corpus_name in corpora:
        yielded = 0
        with read_corpus_file(corpus_name, data_dir=data_dir) as archive:
            for member in archive:
                if only is not None and only not in member.name:
                    continue
                if limit is not None and yielded >= limit:
                    break
                yield corpus_name, member.name
                yielded += 1


def iter_corpus_entries_from_refs(
    corpus_name: str,
    member_names: tuple[str, ...],
    *,
    data_dir: pathlib.Path = DATA_DIR,
) -> Generator[CorpusEntry, None, None]:
    """Load selected corpus members while scanning the archive once."""
    requested = tuple(member_names)
    pending = set(requested)
    schemas: dict[str, dict[str, Any]] = {}
    with read_corpus_file(corpus_name, data_dir=data_dir) as archive:
        for member in archive:
            if member.name not in pending:
                continue
            extracted = archive.extractfile(member)
            if extracted is None:
                continue
            schemas[member.name] = json_loads(extracted.read())
            pending.remove(member.name)
            if not pending:
                break
    if pending:
        missing = ", ".join(sorted(pending))
        raise FileNotFoundError(f"{corpus_name}: {missing}")
    for member_name in requested:
        yield CorpusEntry(corpus=corpus_name, name=member_name, schema=schemas[member_name])


def iter_corpus_streaming(
    corpus: str | None = None,
    *,
    only: str | None = None,
    limit: int | None = None,
    data_dir: pathlib.Path = DATA_DIR,
) -> Generator[CorpusEntry, None, None]:
    """Stream entries from one corpus tarball (or all of them when `corpus` is None).

    Filters by member-name substring before decoding JSON, so skipped entries
    cost only a tar header read instead of a full json.loads.
    """
    corpora = [corpus] if corpus else list(CORPUS_NAMES)
    for corpus_name in corpora:
        yielded = 0
        with read_corpus_file(corpus_name, data_dir=data_dir) as archive:
            for member in archive:
                if only is not None and only not in member.name:
                    continue
                if limit is not None and yielded >= limit:
                    break
                extracted = archive.extractfile(member)
                if extracted is None:
                    continue
                yield CorpusEntry(
                    corpus=corpus_name,
                    name=member.name,
                    schema=json_loads(extracted.read()),
                )
                yielded += 1


if __name__ == "__main__":
    schemas = parse_schemas(CATALOG_DIR)
    create_tar_gz(schemas, DATA_DIR)
