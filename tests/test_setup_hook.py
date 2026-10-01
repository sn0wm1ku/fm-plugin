#!/usr/bin/env python3
"""Offline SessionStart checks; no dependencies installed or licenses accepted."""
import json
import os
from pathlib import Path
import subprocess
import shlex
import sys
import tempfile


def main():
    project = Path(__file__).resolve().parents[1]
    source = project / "scripts/fm_setup_hook.sh"
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        hook = root / "scripts/fm_setup_hook.sh"
        hook.parent.mkdir()
        hook.write_text(source.read_text())
        setup = hook.with_name("fm_setup.py")
        receipt = root / ".local/share/fm-plugin/setup.json"
        receipt.parent.mkdir(parents=True)
        receipt.write_text(json.dumps({"python": sys.executable}))
        env = {**os.environ, "HOME": str(root)}

        def run(client="codex", environment=env):
            manifest = json.loads((project / ("." + client + "-plugin/plugin.json")).read_text())
            configured = manifest["hooks"]["SessionStart"][0]["hooks"][0]
            variable = "PLUGIN_ROOT" if client == "codex" else "CLAUDE_PLUGIN_ROOT"
            assert configured["timeout"] == 120
            argv = shlex.split(configured["command"].replace("${" + variable + "}", str(root)))
            assert argv == ["sh", str(hook), client]
            result = subprocess.run(["/bin/sh", *argv[1:]], env=environment, capture_output=True, text=True, timeout=10)
            assert result.returncode == 0, result.stderr
            return result.stdout

        setup.write_text('print(\'{"ok":true,"ready":true}\')\n')
        assert run() == ""
        setup.write_text('print(\'{"ok":false,"errors":[{"type":"PrerequisitesFailed","message":"SDK missing"}]}\')\nraise SystemExit(1)\n')
        for client in ("codex", "claude"):
            output = json.loads(run(client))["hookSpecificOutput"]
            assert output["hookEventName"] == "SessionStart"
            assert "SDK missing" in output["additionalContext"]
            assert "permission" in output["additionalContext"]
            assert client in output["additionalContext"]
        receipt.write_text(json.dumps({"python": "relative-python"}))
        assert "absolute path" in json.loads(run())["hookSpecificOutput"]["additionalContext"]
        receipt.write_text(json.dumps({"python": str(root / "removed-python")}))
        assert "removed-python" in json.loads(run())["hookSpecificOutput"]["additionalContext"]
        assert "Python 3.10" in json.loads(run(environment={**env, "PATH": ""}))["hookSpecificOutput"]["additionalContext"]
        assert json.loads(receipt.read_text())["python"] == str(root / "removed-python")
    print("Setup hook checks passed (offline).")


if __name__ == "__main__":
    main()
