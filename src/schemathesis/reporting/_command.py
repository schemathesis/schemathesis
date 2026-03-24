from __future__ import annotations

import os
import sys


def get_command_representation() -> str:
    """Get how the current process was invoked."""
    basename = os.path.basename(sys.argv[0])
    args = " ".join(sys.argv[1:])
    if basename in ("schemathesis", "st") or sys.argv[0].endswith(("schemathesis", "st")):
        return f"st {args}"
    if "pytest" in basename:
        return f"pytest {args}"
    return "<unknown entrypoint>"
