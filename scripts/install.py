#!/usr/bin/env python3
"""Install the local marketplace plugin only after FM setup succeeds."""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

from fm_setup import check_installation


def install(client, plugin_id, root):
    checked = check_installation()
    if not checked["ok"]:
        return checked
    if not re.fullmatch(r"fm@[a-zA-Z0-9_-]+", plugin_id):
        return failed("Choose fm@ followed by its configured marketplace name")
    executable = shutil.which(client)
    if not executable:
        return failed("Install {} before installing its FM plugin".format(client))
    action = "install"
    if client == "claude":
        try:
            listing = subprocess.run([executable, "plugin", "list", "--json"],
                                     capture_output=True, text=True, timeout=30)
            if listing.returncode:
                return failed(listing.stderr.strip() or "Cannot inspect installed Claude plugins")
            entries = json.loads(listing.stdout)
            if not isinstance(entries, list):
                return failed("Unexpected installed-plugin listing")
            action = "update" if any(item.get("id") == plugin_id and item.get("scope") == "user"
                                     for item in entries if isinstance(item, dict)) else "install"
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            return failed(str(error))
    commands = [
        [executable, "plugin", "marketplace", "add", str(root)] + (["--json"] if client == "codex" else []),
        ([executable, "plugin", "add", plugin_id, "--json"] if client == "codex" else
         [executable, "plugin", action, plugin_id, "--scope", "user", "--json"]),
    ]
    for command in commands:
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.TimeoutExpired) as error:
            return failed(str(error))
        if result.returncode:
            return failed(result.stderr.strip() or result.stdout.strip() or "Plugin manager failed")
    return {"ok": True, "installation": "complete", "client": client,
            "plugin": plugin_id, "message": "Start a new session to load the plugin."}


def failed(message):
    return {"ok": False, "installation": "failed", "phase": "plugin_copy",
            "errors": [{"type": "InstallationFailed", "message": message}]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", required=True, choices=("codex", "claude"))
    parser.add_argument("--plugin-id", default="fm@apple-fm")
    args = parser.parse_args()
    result = install(args.client, args.plugin_id, Path(__file__).resolve().parents[1])
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
