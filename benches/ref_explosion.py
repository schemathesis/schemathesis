"""Peak-RSS memory gate for the in-tree generator on large, ref-heavy schemas.

The in-tree generator keeps refs symbolic (`inline_budget=0`) and shares one
strategy per uri, so peak RSS on renovate-42 / vega-lite / kestra must stay under
the gate. Each schema runs in its own address-space-capped subprocess.

    python benches/ref_explosion.py            # run the gate, table + exit code
    python benches/ref_explosion.py --child <schema.json>   # internal
"""

from __future__ import annotations

import json
import os
import resource
import subprocess
import sys
from pathlib import Path

GATE_BYTES = 500 * 1024 * 1024
# Cap the address space low so a runaway abort fasts instead of taking down the host.
CAP_BYTES = 2 * 1024 * 1024 * 1024
DRAWS = 25
TIMEOUT_SECONDS = 600

SCHEMA_NAMES = ["renovate-42", "vega-lite", "kestra-0.19.0"]


def _find_corpus() -> Path:
    override = os.environ.get("REF_CORPUS")
    if override:
        return Path(override)
    for base in [Path.cwd(), *Path(__file__).resolve().parents]:
        candidate = base / "test-corpus" / ".schemastore-cache" / "schemas" / "json"
        if candidate.is_dir():
            return candidate
    raise SystemExit("corpus not found; set REF_CORPUS to the schemastore json dir")


def _new_strategy(schema: object):
    import jsonschema_rs

    from schemathesis.generation.jsonschema import StrategyContext
    from schemathesis.generation.jsonschema.strategy import from_schema

    return from_schema(jsonschema_rs.canonicalize(schema, inline_budget=0), StrategyContext())


def _draw(strategy, count: int) -> None:
    from hypothesis import HealthCheck, given, settings

    @given(strategy)
    @settings(max_examples=count, deadline=None, database=None, suppress_health_check=list(HealthCheck))
    def run(value: object) -> None:
        pass

    run()


def _peak_rss_bytes() -> int:
    # `ru_maxrss` is kilobytes on Linux.
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024


def _child(schema_file: str) -> int:
    resource.setrlimit(resource.RLIMIT_AS, (CAP_BYTES, CAP_BYTES))
    schema = json.loads(Path(schema_file).read_text())
    result = {"status": "ok", "stage": "draw"}
    try:
        strategy = _new_strategy(schema)
    except MemoryError:
        result = {"status": "capped", "stage": "build"}
    except BaseException as error:  # noqa: BLE001
        result = {"status": "error", "stage": "build", "error": f"{type(error).__name__}: {error}"[:200]}
    else:
        try:
            _draw(strategy, DRAWS)
        except MemoryError:
            result = {"status": "capped", "stage": "draw"}
        except BaseException as error:  # noqa: BLE001
            result = {"status": "error", "stage": "draw", "error": f"{type(error).__name__}: {error}"[:200]}
    result["peak_rss"] = _peak_rss_bytes()
    print(json.dumps(result))
    return 0


def _format_bytes(value: int) -> str:
    size = float(value)
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"


def _run_case(schema_file: Path) -> dict:
    proc = subprocess.run(
        [sys.executable, __file__, "--child", str(schema_file)],
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SECONDS,
        check=False,
    )
    line = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    if proc.returncode != 0 or not line:
        # A negative return code is a signal (e.g. SIGABRT from a hard allocation failure).
        return {"status": "killed", "peak_rss": 0, "error": f"rc={proc.returncode} {proc.stderr.strip()[-160:]}"}
    return json.loads(line)


def main() -> int:
    corpus = _find_corpus()
    rows = [(name, _run_case(corpus / f"{name}.json")) for name in SCHEMA_NAMES]

    def cell(case: dict) -> str:
        peak = _format_bytes(case["peak_rss"]) if case["peak_rss"] else "-"
        if case["status"] == "ok":
            return f"{peak}"
        if case["status"] in ("capped", "killed"):
            return f">{_format_bytes(CAP_BYTES)} (OOM)"
        return f"{case['status']}: {case.get('error', case.get('stage', ''))}"

    print(f"\nMemory gate: in-tree generator < {_format_bytes(GATE_BYTES)} (cap {_format_bytes(CAP_BYTES)}, {DRAWS} draws)\n")
    print(f"| {'schema':16} | {'peak':28} | gate |")
    print(f"|{'-' * 18}|{'-' * 30}|------|")
    failed = []
    for name, case in rows:
        ok = case["status"] == "ok" and case["peak_rss"] < GATE_BYTES
        if not ok:
            failed.append(name)
        print(f"| {name:16} | {cell(case):28} | {'PASS' if ok else 'FAIL'} |")
    print()
    if failed:
        print(f"Gate FAILED for: {', '.join(failed)}")
        return 1
    print("Gate PASSED")
    return 0


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--child":
        sys.exit(_child(sys.argv[2]))
    sys.exit(main())
