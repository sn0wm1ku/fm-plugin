#!/usr/bin/env python3
"""Launch the MCP bridge using the Python interpreter recorded during setup."""
import json
import os
from pathlib import Path
import sys


def main():
    receipt = Path.home() / ".local/share/fm-plugin/setup.json"
    try:
        if receipt.exists():
            stored = json.loads(receipt.read_text(encoding="utf-8"))
            python = stored.get("python") if isinstance(stored, dict) else None
            if not isinstance(python, str) or not Path(python).is_absolute():
                raise ValueError("Setup receipt needs an absolute Python interpreter path")
        else:
            python = sys.executable
        # Interpreter selection is not authorization; each task rechecks setup.
        server = Path(__file__).resolve().with_name("fm_mcp.py")
        os.execv(python, [python, str(server)])
    except (OSError, ValueError) as error:
        print("FM bridge startup failed: " + str(error) +
              ". Run fm:ask to repair prerequisites with your chosen Python and restart the plugin.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
