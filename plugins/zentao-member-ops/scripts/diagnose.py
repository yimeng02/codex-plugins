#!/usr/bin/env python3
"""Run a safe, read-only diagnostic of the ZenTao member connector."""

from __future__ import annotations

import json
import sys
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "mcp"))

from server import ZentaoMemberTools  # noqa: E402


def main() -> int:
    tools = ZentaoMemberTools()
    status = tools.connection_status({})
    status["available_tool_names"] = [tool["name"] for tool in tools.tool_definitions()]
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0 if status.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
