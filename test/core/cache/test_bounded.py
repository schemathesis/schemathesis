from __future__ import annotations

import sys
import threading

from schemathesis.core.cache import MISSING, BoundedCache


def test_get_survives_concurrent_eviction():
    # Workers share these caches; a read of the key another worker is evicting must not raise.
    cache = BoundedCache(maxsize=1)
    cache[0] = 0
    stop = threading.Event()
    errors: list[Exception] = []

    def churn() -> None:
        i = 1
        while not stop.is_set():
            cache[0] = 0
            cache[i] = i
            i += 1

    worker = threading.Thread(target=churn)
    original_interval = sys.getswitchinterval()
    # Under the GIL the check-then-act window is a handful of bytecodes; without this the
    # scheduler practically never lands inside it, while free-threaded builds hit it on their own.
    sys.setswitchinterval(1e-6)
    worker.start()
    try:
        for _ in range(100_000):
            try:
                cache.get(0)
            except Exception as exc:
                errors.append(exc)
                break
    finally:
        stop.set()
        worker.join()
        sys.setswitchinterval(original_interval)

    assert not errors, errors[0]


def test_get_returns_default_for_absent_key():
    assert BoundedCache(maxsize=1).get("absent") is MISSING
    assert BoundedCache(maxsize=1).get("absent", None) is None


def test_evicts_least_recently_used():
    cache = BoundedCache(maxsize=2)
    cache["a"] = 1
    cache["b"] = 2
    cache.get("a")
    cache["c"] = 3

    assert cache.get("b") is MISSING
    assert cache.get("a") == 1
    assert cache.get("c") == 3
