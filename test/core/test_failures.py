from pathlib import Path

from schemathesis.core.failures import get_origin


def origin_of_assertion_at(path: Path) -> tuple:
    code = compile("raise AssertionError('boom')", str(path), "exec")
    try:
        exec(code, {})
    except AssertionError as exc:
        return get_origin(exc)
    raise RuntimeError("unreachable")


def test_origin_is_the_same_in_two_checkouts(tmp_path, monkeypatch):
    # Origins end up in stored baseline entries, so the checkout root must not be part of them.
    origins = []
    for name in ("checkout-a", "checkout-b"):
        root = tmp_path / name
        (root / "tests").mkdir(parents=True)
        monkeypatch.chdir(root)
        origins.append(origin_of_assertion_at(root / "tests" / "checks.py"))

    assert origins[0] == origins[1]
