from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Iterable, Mapping, Sized
from dataclasses import dataclass
from typing import TYPE_CHECKING

from hypothesis import strategies as st

from schemathesis.specs.openapi.adapter.responses import OpenApiResponses

if TYPE_CHECKING:
    from schemathesis.generation.case import Case
    from schemathesis.schemas import APIOperation

MIN_OPERATION_WEIGHT = 1
MAX_OPERATION_WEIGHT = 8


@dataclass
class OperationStrategy:
    operation: APIOperation
    strategy: st.SearchStrategy[Case]


def _stable_fraction(value: str) -> float:
    digest = hashlib.blake2b(value.encode("utf-8"), digest_size=8).digest()
    raw = int.from_bytes(digest, byteorder="big", signed=False)
    return raw / float(2**64 - 1)


def _path_shape(path: str) -> tuple[int, bool]:
    depth = len([segment for segment in path.split("/") if segment])
    return depth, "{" in path and "}" in path


def _response_complexity(operation: APIOperation) -> int:
    responses = operation.responses
    if isinstance(responses, OpenApiResponses):
        return len(responses.status_codes)
    if isinstance(responses, Mapping):
        return len(responses)
    if isinstance(responses, Sized):
        return len(responses)
    if isinstance(responses, Iterable):
        return sum(1 for _ in responses)
    return 0


def _operation_complexity(operation: APIOperation) -> int:
    parameters = sum(1 for _ in operation.iter_parameters())
    try:
        body_variants = len(operation.body)
    except TypeError:
        body_variants = sum(1 for _ in operation.body)
    responses = _response_complexity(operation)
    return parameters + body_variants + responses


def _calculate_operation_weight(
    operation: APIOperation,
    *,
    method_counts: Counter[str],
    path_shape_counts: Counter[tuple[int, bool]],
    seed: int | None,
) -> int:
    method_frequency = method_counts[operation.method.upper()]
    shape_frequency = path_shape_counts[_path_shape(operation.path)]

    rarity_component = (1.0 / method_frequency) + (1.0 / shape_frequency)

    complexity = min(_operation_complexity(operation), 12)
    complexity_component = (complexity / 12.0) * 0.75

    seed_marker = seed if seed is not None else 0
    jitter_component = _stable_fraction(f"{seed_marker}:{operation.label}") * 0.15

    score = (rarity_component * 2.0) + complexity_component + jitter_component
    weight = int(round(score * 2))
    return max(MIN_OPERATION_WEIGHT, min(MAX_OPERATION_WEIGHT, weight))


def build_weighted_operation_table(
    operations: list[OperationStrategy], *, seed: int | None, worker_id: int
) -> list[OperationStrategy]:
    sorted_operations = sorted(operations, key=lambda item: item.operation.label)
    if len(sorted_operations) <= 1:
        return sorted_operations

    method_counts = Counter(item.operation.method.upper() for item in sorted_operations)
    path_shape_counts = Counter(_path_shape(item.operation.path) for item in sorted_operations)

    weighted: list[OperationStrategy] = []
    for item in sorted_operations:
        weight = _calculate_operation_weight(
            item.operation,
            method_counts=method_counts,
            path_shape_counts=path_shape_counts,
            seed=seed,
        )
        weighted.extend([item] * weight)

    if len(weighted) <= 1:
        return weighted

    seed_offset = int(_stable_fraction(str(seed if seed is not None else 0)) * len(weighted))
    offset = (seed_offset + worker_id) % len(weighted)
    if offset:
        weighted = weighted[offset:] + weighted[:offset]
    return weighted
