"""Exercise MCP protocol, subprocess relay and cleanup; --live also invokes Apple FM."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

from mcp import Client
from mcp.client.stdio import StdioServerParameters

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import fm_bridge as bridge
import fm_mcp
import test_callbacks

TOOLS = [{"name": "lookup_inventory", "description": "Find the current inventory stock for a product code.",
          "inputSchema": {"type": "object", "properties": {"code": {"type": "string"}},
                          "required": ["code"], "additionalProperties": False}}]


def script_tools(mode="stock", *extra):
    return [{**TOOLS[0], "command": [sys.executable, str(Path(__file__).resolve()), "--callback", mode, *extra]}]


async def worker(directory, relay):
    prompt = sys.stdin.read()
    if prompt == "wait":
        await asyncio.sleep(60)
    if prompt == "fail":
        print(json.dumps(bridge.failure("SetupRequired", "Explicit agreement required")))
        return
    if relay:
        value = await bridge.exchange(Path(relay), "lookup_inventory", {"code": "FM-DEMO-7"}, 20)
        entries = [{"role": "tool", "toolName": "lookup_inventory",
                    "contents": [{"type": "text", "text": json.dumps(value)}]}]
        bridge.write_json(Path(directory) / "transcript.json", {"transcript": {"entries": entries}})
        print(json.dumps({"ok": True, "result": value["result"]} if value["ok"] else value))
    else:
        print(json.dumps({"ok": True, "result": "Hello"}))


async def until(client, value, status):
    for _ in range(60):
        if value["status"] == status:
            return value
        assert value["status"] == "running", value
        value = (await client.call_tool("fm_continue", {"session_id": value["session_id"], "replies": []})).structured_content
    raise AssertionError("Timed out waiting for " + status)


async def offline():
    paths, processes = [], []

    async def spawn(directory, relay, instructions, timeout):
        paths.extend([directory, *([relay] if relay else [])])
        process = await asyncio.create_subprocess_exec(
            sys.executable, str(Path(__file__).resolve()), "--worker", str(directory), str(relay or ""),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        processes.append(process)
        return process

    with patch.object(fm_mcp, "spawn_worker", spawn):
        async with Client(fm_mcp.create_server(), mode="legacy") as client:
            listed = await client.list_tools()
            assert {tool.name for tool in listed.tools} == {"fm_start", "fm_continue", "fm_cancel", "fm_run"}
            metadata = next(tool for tool in listed.tools if tool.name == "fm_start")
            assert "programmatic" in metadata.description and "execute" in metadata.description
            automated = next(tool for tool in listed.tools if tool.name == "fm_run")
            assert "automatically" in automated.description and "No host LLM" in automated.description
            assert "tools" in metadata.input_schema["required"], metadata
            for arguments in ({"prompt": "hello"}, {"prompt": "hi", "tools": [], "timeout_seconds": 0},
                              {"prompt": "hi", "tools": [], "timeout_seconds": True},
                              {"prompt": "hi", "tools": [{"name": "bad"}]},
                              {"prompt": "hi", "tools": [{**TOOLS[0], "extra": "not allowed"}]},
                              {"prompt": "hi", "tools": [{**TOOLS[0], "inputSchema": {"type": "string"}}]}):
                assert (await client.call_tool("fm_start", arguments)).is_error, arguments
            assert not paths
            for invalid in ([{**script_tools()[0], "command": ["python3"]}],
                            [{**script_tools()[0], "command": []}],
                            [{**script_tools()[0], "extra": True}]):
                assert (await client.call_tool("fm_run", {"prompt": "inventory", "tools": invalid})).is_error
            assert not paths
            automatic = (await client.call_tool("fm_run", {"prompt": "inventory", "tools": script_tools()})).structured_content
            assert automatic["status"] == "completed" and automatic["result"] == {"stock": 37}, automatic
            assert automatic["tool_calls"] == [{"name": "lookup_inventory", "ok": True}], automatic
            assert "tool_evidence" not in automatic, "Automatic results should not repeat full tool payloads"
            denied_auto = (await client.call_tool("fm_run", {"prompt": "inventory", "tools": script_tools("denied")})).structured_content
            assert denied_auto["status"] == "failed" and denied_auto["errors"][0]["type"] == "Denied", denied_auto
            plain = (await client.call_tool("fm_run", {"prompt": "hello", "tools": []})).structured_content
            assert plain["status"] == "completed" and plain["result"] == "Hello", plain
            with tempfile.TemporaryDirectory() as directory:
                timed_path = Path(directory) / "timed.json"
                timed_auto = (await client.call_tool("fm_run", {
                    "prompt": "inventory", "tools": script_tools("family", str(timed_path)), "timeout_seconds": 5})).structured_content
                assert timed_auto["status"] == "failed", timed_auto
                await test_callbacks.stopped(timed_path)
                cancelled_path = Path(directory) / "cancelled.json"
                task = asyncio.create_task(client.call_tool("fm_run", {
                    "prompt": "inventory", "tools": script_tools("family", str(cancelled_path)), "timeout_seconds": 30}))
                for _ in range(200):
                    if cancelled_path.exists():
                        break
                    await asyncio.sleep(0.02)
                assert cancelled_path.exists()
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                await test_callbacks.stopped(cancelled_path)
            for _ in range(100):
                if all(not path.exists() for path in paths):
                    break
                await asyncio.sleep(0.02)
            assert all(not path.exists() for path in paths), "fm_run must clean up without follow-up calls"
            previous = len(processes)
            starting = asyncio.create_task(client.call_tool("fm_run", {
                "prompt": "wait", "tools": [], "timeout_seconds": 30}))
            for _ in range(100):
                if len(processes) > previous:
                    break
                await asyncio.sleep(0.01)
            assert len(processes) > previous
            starting.cancel()
            try:
                await starting
            except asyncio.CancelledError:
                pass
            for _ in range(200):
                if all(not path.exists() for path in paths):
                    break
                await asyncio.sleep(0.02)
            assert all(not path.exists() for path in paths), "Cancelling fm_run during initial snapshot must clean up"
            failed = await client.call_tool("fm_continue", {"session_id": "0" * 32, "replies": []})
            assert failed.is_error and failed.structured_content["errors"][0]["type"] == "UnknownSession"
            started = (await client.call_tool("fm_start", {"prompt": "inventory", "tools": TOOLS})).structured_content
            pending = await until(client, started, "tool_requests")
            request = pending["requests"][0]
            assert request["name"] == "lookup_inventory" and request["arguments"] == {"code": "FM-DEMO-7"}
            identity = pending["session_id"]
            response = {"request_id": request["request_id"], "response": {"ok": True, "result": {"stock": 37}}}
            for replies in ([response, response], [response, {**response, "request_id": "0" * 32}],
                            [{**response, "response": {"ok": True}}]):
                invalid = await client.call_tool("fm_continue", {"session_id": identity, "replies": replies})
                assert invalid.is_error, invalid
                still_pending = (await client.call_tool("fm_continue", {"session_id": identity, "replies": []})).structured_content
                assert still_pending["requests"][0]["request_id"] == request["request_id"]
            finished = (await client.call_tool("fm_continue", {"session_id": identity, "replies": [response]})).structured_content
            finished = await until(client, finished, "completed")
            assert finished["result"] == {"stock": 37}
            assert finished["tool_evidence"][0]["name"] == "lookup_inventory"
            assert "37" in json.dumps(finished["tool_evidence"])
            assert (await client.call_tool("fm_continue", {"session_id": identity, "replies": [response]})).is_error
            assert all(not path.exists() for path in paths)
            denied = (await client.call_tool("fm_start", {"prompt": "inventory", "tools": TOOLS})).structured_content
            denied = await until(client, denied, "tool_requests")
            denial = {"request_id": denied["requests"][0]["request_id"],
                      "response": {"ok": False, "errors": [{"type": "Denied", "message": "Host denied access"}]}}
            failed = await client.call_tool("fm_continue", {"session_id": denied["session_id"], "replies": [denial]})
            value = await until(client, failed.structured_content, "failed")
            assert value["errors"][0]["type"] == "Denied"
            failed = await client.call_tool("fm_start", {"prompt": "fail", "tools": []})
            value = await until(client, failed.structured_content, "failed")
            assert value["errors"][0]["type"] == "SetupRequired"
            started = (await client.call_tool("fm_start", {"prompt": "wait", "tools": []})).structured_content
            cancelled = await client.call_tool("fm_cancel", {"session_id": started["session_id"]})
            assert cancelled.is_error and cancelled.structured_content["errors"][0]["type"] == "Cancelled"
            assert all(not path.exists() for path in paths)
            timed = (await client.call_tool("fm_start", {"prompt": "wait", "tools": [], "timeout_seconds": 5})).structured_content
            await asyncio.sleep(5)
            assert all(not path.exists() for path in paths), "watchdog needs no host polling"
            timed = (await client.call_tool("fm_continue", {"session_id": timed["session_id"], "replies": []})).structured_content
            assert timed["errors"][0]["type"] == "TimeoutError"
            expired = await client.call_tool("fm_continue", {"session_id": timed["session_id"], "replies": [response]})
            assert expired.is_error and expired.structured_content["errors"][0]["type"] == "ExpiredSession"
            await client.call_tool("fm_start", {"prompt": "wait", "tools": TOOLS})
        assert all(not path.exists() for path in paths), "disconnect must clean private files"
        assert all(process.returncode is not None for process in processes), "disconnect must stop workers"
    print("PASS: MCP protocol, automatic script execution, input boundaries, relay, denial, timeout, initial/callback cancellation and disconnect cleanup")


async def live():
    server = StdioServerParameters(command=sys.executable, args=[str(Path(fm_mcp.__file__))])
    async with Client(server, mode="legacy") as client:
        finished = await client.call_tool("fm_run", {
            "prompt": "Use lookup_inventory to check FM-DEMO-7 and report its stock.", "tools": script_tools(),
            "instructions": "Always call lookup_inventory for stock. Never guess. Report the returned stock.",
            "timeout_seconds": 120})
        value = finished.structured_content
        assert value["status"] == "completed" and "37" in value["result"], value
        assert value["tool_calls"] == [{"name": "lookup_inventory", "ok": True}], value
        print("PASS: one real stdio MCP call -> native Apple FM -> executable callback -> final answer")


if __name__ == "__main__":
    if "--callback" in sys.argv:
        if sys.argv[2] == "stock":
            arguments = json.load(sys.stdin)
            assert arguments == {"code": "FM-DEMO-7"}, arguments
            print(json.dumps({"ok": True, "result": {"stock": 37}}))
        else:
            test_callbacks.worker(sys.argv[2], sys.argv[3:])
    elif "--worker" in sys.argv:
        asyncio.run(worker(sys.argv[2], sys.argv[3]))
    else:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--live", action="store_true")
        args = parser.parse_args()
        asyncio.run(offline())
        if args.live:
            asyncio.run(live())
