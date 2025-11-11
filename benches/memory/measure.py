import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any


def run_memray(schema_path: str, args: list[str]) -> dict[str, Any]:
    with tempfile.NamedTemporaryFile(suffix=".bin") as tmp:
        memray_output = tmp.name

        stats_output = memray_output + ".json"
        cmd = (
            ["memray", "run", "--force", "-o", memray_output, "-m", "schemathesis.cli", "run"]
            + args
            + ["-u http://127.0.0.1:8000", "--seed=1", schema_path]
        )

        subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            text=True,
            env={**os.environ, "SCHEMATHESIS_HOOKS": "benches.memory.hooks"},
        )

        subprocess.run(
            ["memray", "stats", "--force", "-o", stats_output, "--json", memray_output],
            stdout=subprocess.PIPE,
            text=True,
        )

        with open(stats_output) as f:
            stats = json.load(f)

        return {
            "peak_memory": stats["metadata"]["peak_memory"],
            "total_bytes_allocated": stats["total_bytes_allocated"],
            "total_num_allocations": stats["total_num_allocations"],
        }


def format_bytes(value: int | float) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if value < 1024.0:
            return f"{value:.2f} {unit}"
        value /= 1024.0
    return f"{value:.2f} TB"


HERE = Path(__file__).parent


def main():
    scenarios_path = HERE / "scenarios.json"
    with open(scenarios_path) as f:
        config = json.load(f)

    schemas_dir = HERE / "schemas"
    results = []

    print("Running memory benchmarks...")  # noqa: T201
    for scenario in config["scenarios"]:
        print(f"  Running {scenario['name']}...")  # noqa: T201
        schema_path = schemas_dir / scenario["schema"]

        result = run_memray(str(schema_path), scenario["args"])
        result["name"] = scenario["name"]
        result["schema"] = scenario["schema"]
        result["args"] = " ".join(scenario["args"])
        results.append(result)

    report = ["## 📊 Memory Benchmark Results\n"]
    report.append("| Scenario | Schema | Peak Memory | Total Allocated | Total Allocations |")
    report.append("|----------|--------|-------------|-----------------|-------------------|")

    for r in results:
        peak = format_bytes(r["peak_memory"])
        total = format_bytes(r["total_bytes_allocated"])
        allocations = f"{r['total_num_allocations']:,}"

        report.append(f"| {r['name']} | {r['schema']} | {peak} | {total} | {allocations} |")

    report.append("\n<details>")
    report.append("<summary>📝 Benchmark Details</summary>\n")

    for r in results:
        report.append(f"### {r['name']}")
        report.append(f"- **Command args**: `{r['args']}`")
        report.append(f"- **Peak Memory**: {r['peak_memory']:,} bytes")
        report.append(f"- **Total Allocated**: {r['total_bytes_allocated']:,} bytes")
        report.append(f"- **Total Allocations**: {r['total_num_allocations']:,}")
        report.append("")

    report.append("</details>")

    output_dir = Path(os.environ.get("GITHUB_WORKSPACE", ".")) / "memory-benchmark-results"
    output_dir.mkdir(exist_ok=True)

    with open(output_dir / "results.json", "w") as f:
        json.dump(results, f, indent=2)

    with open(output_dir / "report.md", "w") as f:
        f.write("\n".join(report))

    print(f"Results saved to {output_dir}")  # noqa: T201


if __name__ == "__main__":
    main()
