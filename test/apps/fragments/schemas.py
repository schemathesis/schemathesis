from __future__ import annotations

from typing import Any


def success() -> dict[str, Any]:
    return {"/api/success": {"get": {"responses": {"200": {"description": "Success"}}}}}
