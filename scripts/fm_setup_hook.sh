#!/bin/sh
# Shared SessionStart check; installation and agreement require the human.
if ! command -v python3 >/dev/null 2>&1; then
    printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"FM setup requires Python 3.10 or newer. Use the fm:ask setup repair flow: explain the missing prerequisite, ask permission before installing, and recheck. Never accept terms on the user behalf."}}'
    exit 0
fi
exec python3 - "$0" "$@" <<'PY'
import json
from pathlib import Path
import subprocess
import sys


def status():
    receipt = Path.home() / ".local/share/fm-plugin/setup.json"
    try:
        saved = json.loads(receipt.read_text(encoding="utf-8")) if receipt.exists() else {}
        interpreter = saved.get("python", sys.executable)
        if not isinstance(interpreter, str) or not Path(interpreter).is_absolute():
            return {"ok": False, "errors": [{"type": "SetupReceiptInvalid", "message": "Recorded Python must be an absolute path."}]}
        result = subprocess.run([interpreter, str(Path(sys.argv[1]).resolve().with_name("fm_setup.py")), "status"],
                                capture_output=True, text=True, timeout=90)
        try:
            parsed = json.loads(result.stdout)
        except ValueError:
            return {"ok": False, "errors": [{"type": "SetupCheckFailed", "message": result.stdout + result.stderr}], "exit_code": result.returncode}
        return parsed if isinstance(parsed, dict) and result.returncode == 0 else {
            "ok": False, "diagnostics": parsed, "stderr": result.stderr, "exit_code": result.returncode}
    except (OSError, ValueError, AttributeError, subprocess.TimeoutExpired) as error:
        return {"ok": False, "errors": [{"type": "SetupCheckFailed", "message": str(error)}]}


checked = status()
if checked.get("ok") is not True or checked.get("ready") is not True:
    client = sys.argv[2] if len(sys.argv) > 2 else "host"
    context = ("FM setup needs attention in " + client + ". Use the fm:ask setup repair flow. "
               "Explain the failed checks and request permission before installing missing dependencies. "
               "Display actual terms and ask Agree or Reject when required. Native license acceptance must be done by the human. "
               "Recheck setup before inference. Diagnostics: " + json.dumps(checked, ensure_ascii=False))
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": context}}, ensure_ascii=False))
PY
