import subprocess
import sys

# The CLI loads before any command runs, so `st --help` should not pay for generation, HTTP or reporting libraries.
_PROBE = "import sys, schemathesis.cli; print(sorted(m for m in sys.modules if m.split('.')[0] in {'hypothesis', 'requests', 'httpx'} or m.startswith('schemathesis.reporting') or '.error_feedback.parsers' in m))"


def test_cli_startup_does_not_import_heavy_modules():
    output = subprocess.run([sys.executable, "-c", _PROBE], capture_output=True, text=True, check=True).stdout
    assert output.strip() == "[]"
