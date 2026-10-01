"""Check automatic configured-tool dispatch and inspect real Claude MCP tools."""
import argparse
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from mcp import Client
from mcp.client.stdio import StdioServerParameters

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import fm_bridge as bridge
import fm_claude
import fm_mcp

SCHEMA = {"type": "object", "properties": {"code": {"type": "string"}},
          "required": ["code"], "additionalProperties": False}
POLICY = {"sample:lookup": SCHEMA}


async def inspect():
    async with Client(StdioServerParameters(command="claude", args=["mcp", "serve"]), mode="legacy") as client:
        page = await client.list_tools()
        print(json.dumps({"names": [tool.name for tool in page.tools],
                          "read": [{"name": tool.name, "inputSchema": tool.input_schema}
                                   for tool in page.tools if "read" in tool.name.lower()]}, indent=2))


async def worker(directory, relay):
    prompt = sys.stdin.read()
    if prompt == "wait":
        await asyncio.sleep(60)
        return
    responses = [await bridge.exchange(Path(relay), "lookup", {"code": code}, 20) for code in ("A", "B")]
    failed = next((response for response in responses if not response["ok"]), None)
    print(json.dumps(failed or {"ok": True, "result": [response["result"] for response in responses]}))


async def offline():
    calls, paths, processes, closed = [], [], [], []
    metadata = {}

    async def list_tools(cursor=None):
        return SimpleNamespace(tools=[SimpleNamespace(name="lookup", description="Look up stock", input_schema=SCHEMA,
            model_dump=lambda **kwargs: {"_meta": metadata})], next_cursor=None)

    async def call_tool(name, arguments, read_timeout_seconds):
        calls.append((name, arguments))
        return SimpleNamespace(structured_content={"stock": 37, "code": arguments["code"]}, content=[], is_error=False)

    @asynccontextmanager
    async def connect(config, timeout):
        try:
            yield SimpleNamespace(list_tools=list_tools, call_tool=call_tool)
        finally:
            closed.append(True)

    async def spawn(directory, relay, instructions, timeout):
        paths.extend([directory, relay])
        process = await asyncio.create_subprocess_exec(sys.executable, str(Path(__file__).resolve()),
            "--worker", str(directory), str(relay), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        processes.append(process)
        return process

    config = {"mcpServers": {"sample": {"command": "unused"}}}
    with patch.object(fm_claude, "connect", connect), patch.object(fm_mcp, "spawn_worker", spawn):
        result = await fm_claude.run(config, ["sample:lookup"], "inventory", policy=POLICY)
        assert result["ok"] and result["result"] == [{"stock": 37, "code": "A"}, {"stock": 37, "code": "B"}], result
        assert result["tool_calls"] == [{"name": "sample:lookup", "ok": True}] * 2, result
        assert calls == [("lookup", {"code": "A"}), ("lookup", {"code": "B"})]
        assert closed and all(not path.exists() for path in paths)
        assert not (await fm_claude.run(config, ["sample:missing"], "inventory", policy={"sample:missing": SCHEMA}))["ok"]
        assert not (await fm_claude.run(config, ["sample:lookup"], "inventory"))["ok"]
        for invalid in ({"$ref": "https://invalid.example/schema"}, {"type": "invalid"}):
            assert not (await fm_claude.run(config, ["sample:lookup"], "inventory", policy={"sample:lookup": invalid}))["ok"]
        before = len(calls)
        denied = await fm_claude.run(config, ["sample:lookup"], "inventory", policy={"sample:lookup": {
            **SCHEMA, "properties": {"code": {"const": "C"}}}})
        assert not denied["ok"] and denied["errors"][0]["type"] == "ScopeDenied", denied
        assert len(calls) == before, "Out-of-scope arguments must never reach the real tool"
        metadata["anthropic/requiresUserInteraction"] = True
        denied = await fm_claude.run(config, ["sample:lookup"], "inventory", policy=POLICY)
        assert not denied["ok"] and denied["errors"][0]["type"] == "ApprovalRequired", denied
        assert len(calls) == before
        metadata.clear()
        result = await fm_claude.run(config, ["sample:lookup"], "wait", timeout=5, policy=POLICY)
        assert not result["ok"], result
        assert all(not path.exists() for path in paths)
        task = asyncio.create_task(fm_claude.run(config, ["sample:lookup"], "wait", policy=POLICY))
        await asyncio.sleep(0.3)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert all(not path.exists() for path in paths)
        assert all(process.returncode is not None for process in processes)
    failed_client = SimpleNamespace(call_tool=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("secret")))
    error = await fm_claude.tool_reply(failed_client, "lookup", {}, 5)
    assert not error["ok"] and "secret" not in json.dumps(error)
    print("PASS: configured MCP automatic multiple calls, exact bindings, errors, timeout and cancellation cleanup")


async def live():
    path = str(Path(__file__).resolve().parents[1] / "README.md")
    policy = {"claude:Read": {"type": "object", "properties": {
        "file_path": {"const": path}, "limit": {"type": "integer", "minimum": 1, "maximum": 20},
        "offset": {"const": 1}, "pages": {"enum": ["", "1"]}},
        "required": ["file_path", "limit"], "additionalProperties": False}}
    observed = []
    attempted = []
    real_reply = fm_claude.tool_reply
    real_normalize = bridge.normalize

    def normalize(schema, arguments):
        value = real_normalize(schema, arguments)
        if schema.get("type") == "object" and "file_path" in schema.get("properties", {}):
            attempted.append(value)
        return value

    async def record(client, name, arguments, remaining):
        response = await real_reply(client, name, arguments, remaining)
        observed.append((name, arguments, response))
        return response

    with patch.object(fm_claude, "tool_reply", record), patch.object(bridge, "normalize", normalize):
        value = await fm_claude.run(fm_claude.DEFAULT_CONFIG, ["claude:Read"],
            "Call Read with file_path " + path + " and limit 8. Read the file once and report its first heading.",
            instructions="You must use Read to answer. Set limit to 8, offset to 1, and pages to 1. Report the heading from the returned file.",
            policy=policy)
    assert value["ok"], (value, attempted)
    assert observed and all(name == "Read" and arguments["file_path"] == path for name, arguments, _ in observed)
    assert all(response["ok"] and "Apple Foundation Models plugin" in json.dumps(response) for _, _, response in observed), observed
    assert "Apple Foundation Models" in value["result"], value
    print("PASS: native Apple FM -> actual Claude Read via MCP -> scoped README content -> final answer, no host LLM callback")
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "policy.json").write_text(json.dumps(policy))
        (root / "prompt.txt").write_text("Use Read once with file_path " + path +
            ", limit 8, offset 1 and pages 1. Report the first heading from the file.")
        process = await asyncio.create_subprocess_exec(sys.executable, str(Path(fm_claude.__file__)),
            "--tools", "claude:Read", "--policy", str(root / "policy.json"),
            "--prompt-file", str(root / "prompt.txt"), "--timeout", "120",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, _ = await asyncio.wait_for(process.communicate(), 130)
        result = json.loads(stdout)
        assert process.returncode == 0 and result["ok"] and "Apple Foundation Models" in result["result"], result
        assert result["tool_calls"] and all(call["name"] == "claude:Read" and call["ok"] for call in result["tool_calls"]), result
    print("PASS: CLI defaults, setup gate, policy/prompt files and actual Claude Read callback")


if __name__ == "__main__":
    if "--worker" in sys.argv:
        asyncio.run(worker(sys.argv[2], sys.argv[3]))
    else:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--inspect", action="store_true")
        parser.add_argument("--live", action="store_true")
        args = parser.parse_args()
        asyncio.run(inspect() if args.inspect else offline())
        if args.live:
            asyncio.run(live())
