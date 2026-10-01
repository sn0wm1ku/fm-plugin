"""Run actual script callbacks and assert validation, bounds and process cleanup."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import fm_callbacks as callbacks

TOOL = {"name": "lookup", "description": "Look up a code", "inputSchema": {
    "type": "object", "properties": {"code": {"type": "string"}},
    "required": ["code"], "additionalProperties": False}}


def tool(mode, *extra):
    return {**TOOL, "command": [sys.executable, str(Path(__file__).resolve()), "--worker", mode, *extra]}


def worker(mode, extra):
    if mode == "sleep":
        time.sleep(60)
        return
    if mode == "family":
        child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--worker", "sleep"])
        Path(extra[0]).write_text(json.dumps([os.getpid(), child.pid]))
        time.sleep(60)
        return
    arguments = json.load(sys.stdin)
    if mode == "bad":
        print("not json")
    elif mode == "overflow":
        sys.stdout.write("x" * (callbacks.bridge.LIMIT * 32))
    elif mode == "stderr":
        sys.stderr.write("x" * (callbacks.bridge.LIMIT * 32))
    elif mode == "nan":
        print('{"ok":true,"result":NaN}')
    elif mode == "denied":
        print(json.dumps(callbacks.bridge.failure("Denied", "No access")))
    else:
        print(json.dumps({"ok": True, "result": arguments}))
        if mode == "exit":
            sys.exit(7)


async def stopped(path):
    pids = json.loads(path.read_text())
    for _ in range(50):
        states = [subprocess.run(["ps", "-o", "stat=", "-p", str(pid)],
                                 capture_output=True, text=True).stdout.strip() for pid in pids]
        if all(not state or state.startswith("Z") for state in states):
            return
        await asyncio.sleep(0.02)
    raise AssertionError("Callback process survived cleanup: " + str(pids))


async def main():
    assert callbacks.definitions([tool("echo")]) == [TOOL]
    assert callbacks.definitions([]) == []
    args = {"code": "$(touch /tmp/never-executed) ; literal"}
    assert await callbacks.execute(tool("echo"), args, 5) == {"ok": True, "result": args}
    with patch.object(callbacks.asyncio, "create_subprocess_exec", side_effect=AssertionError("must not launch")):
        for registration, arguments in [(tool("echo"), {}), ({**tool("echo"), "command": ["python3"]}, args),
                                        ({**tool("echo"), "command": [sys.executable, "\0"]}, args)]:
            assert not (await callbacks.execute(registration, arguments, 5))["ok"]
    for mode in ("bad", "overflow", "stderr", "nan", "exit", "denied"):
        value = await callbacks.execute(tool(mode), args, 5)
        assert not value["ok"], (mode, value)
    assert (await callbacks.execute(tool("sleep"), args, 0.1))["errors"][0]["type"] == "CallbackTimeout"
    with tempfile.TemporaryDirectory() as directory:
        timed = Path(directory) / "timed.json"
        assert not (await callbacks.execute(tool("family", str(timed)), args, 1))["ok"]
        await stopped(timed)
        cancelled = Path(directory) / "cancelled.json"
        task = asyncio.create_task(callbacks.execute(tool("family", str(cancelled)), args, 30))
        for _ in range(100):
            if cancelled.exists():
                break
            await asyncio.sleep(0.02)
        assert cancelled.exists()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("Cancellation must propagate")
        await stopped(cancelled)
    print("PASS: script callbacks, argument validation, JSON envelopes, output bounds, deadlines and process group cleanup")


if __name__ == "__main__":
    if "--worker" in sys.argv:
        worker(sys.argv[2], sys.argv[3:])
    else:
        asyncio.run(main())
