from __future__ import annotations

import io
import os
import pathlib
import re
import tarfile
from typing import Any, Dict, Generator

import yaml

try:
    from yaml import CSafeLoader as SafeLoader
except ImportError:
    from yaml import SafeLoader  # type: ignore

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


CURRENT_DIR = pathlib.Path(__file__).parent.absolute()
CATALOG_DIR = CURRENT_DIR / "../test-corpus/openapi-directory/APIs/"
DATA_DIR = CURRENT_DIR / "data"

Loader = type("YAMLLoader", (SafeLoader,), {})
Loader.yaml_implicit_resolvers = {  # type: ignore[attr-defined]
    key: [(tag, regexp) for tag, regexp in mapping if tag != "tag:yaml.org,2002:timestamp"]
    for key, mapping in Loader.yaml_implicit_resolvers.copy().items()  # type: ignore[attr-defined]
}

Loader.add_implicit_resolver(  # type: ignore
    "tag:yaml.org,2002:float",
    re.compile(
        r"""^(?:[-+]?(?:[0-9][0-9_]*)\.[0-9_]*(?:[eE][-+]?[0-9]+)?
                    |[-+]?(?:[0-9][0-9_]*)(?:[eE][-+]?[0-9]+)
                    |\.[0-9_]+(?:[eE][-+]?[0-9]+)?
                    |[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+\.[0-9_]*
                    |[-+]?\.(?:inf|Inf|INF)
                    |\.(?:nan|NaN|NAN))$""",
        re.X,
    ),
    list("-+0123456789."),
)


def construct_mapping(self: SafeLoader, node: yaml.Node, deep: bool = False) -> dict[str, Any]:
    if isinstance(node, yaml.MappingNode):
        self.flatten_mapping(node)  # type: ignore
    mapping = {}
    for key_node, value_node in node.value:
        if key_node.tag != "tag:yaml.org,2002:str":
            key = key_node.value
        else:
            key = self.construct_object(key_node, deep)  # type: ignore
        mapping[key] = self.construct_object(value_node, deep)  # type: ignore
    return mapping


Loader.construct_mapping = construct_mapping  # type: ignore


def create_tar_gz(schemas: Dict[str, Dict[str, Any]], output_dir: pathlib.Path) -> None:
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


def parse_schemas(directory: pathlib.Path) -> Dict[str, Dict[str, Any]]:
    schemas: Dict[str, Dict[str, Any]] = {}

    for root, _, files in os.walk(directory):
        for file in files:
            if file.endswith("swagger.yaml") or file.endswith("openapi.yaml"):
                file_path = os.path.join(root, file)
                try:
                    with open(file_path, "r") as fd:
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
                    print(f"Error parsing {file_path}")

    return schemas


def get_schema_version(schema: Dict[str, Any]) -> str:
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


def read_corpus_file(name: str) -> tarfile.TarFile:
    return tarfile.open(DATA_DIR / f"{name}.tar.gz", "r:gz")


def iter_corpus_file(name: str) -> Generator[tuple[str, dict[str, Any]], None, None]:
    """Iterate over the corpus file."""
    with read_corpus_file(name) as tar:
        for member in tar.getmembers():
            extracted = tar.extractfile(member)
            if extracted is not None:
                yield member.name, json_loads(extracted.read())


def iter_all_corpus_files() -> Generator[tuple[str, str, dict[str, Any]], None, None]:
    """Iterate over all corpus files."""
    for corpus_name in os.listdir(DATA_DIR):
        if corpus_name.endswith(".tar.gz"):
            for file_name, schema in iter_corpus_file(corpus_name):
                yield corpus_name, file_name, schema


if __name__ == "__main__":
    schemas = parse_schemas(CATALOG_DIR)
    create_tar_gz(schemas, DATA_DIR)
