"""Check bridge boundaries; --live verifies a real Foundation Models callback."""
import argparse
import asyncio
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace
from unittest.mock import patch

BRIDGE = Path(__file__).resolve().parents[1] / "scripts" / "fm_bridge.py"
bridge = runpy.run_path(str(BRIDGE))
MANIFEST = {"tools": [{"name": "lookup_host_inventory", "description": "Look up the current private inventory stock for a product code.",
    "inputSchema": {"type": "object", "properties": {"code": {"type": "string", "description": "The product code"}},
                    "required": ["code"], "additionalProperties": False}}]}


def rejected(action):
    try:
        action()
    except (ValueError, OSError):
        return
    raise AssertionError("Expected operation to be rejected")


async def pending(session):
    for _ in range(300):
        requests = bridge["poll"](session)
        if requests:
            return requests[0]
        await asyncio.sleep(0.1)
    raise AssertionError("No pending tool request")


async def offline():
    for schema in (
        {"type": "object", "properties": {}, "additionalProperties": True},
        {"type": "object", "properties": {"code": {"type": "string", "minLength": 1}}, "additionalProperties": False},
        {"type": "object", "properties": {"code": {"anyOf": [{"type": "string"}]}}, "additionalProperties": False},
        {"type": "object", "properties": {}, "required": ["missing"], "additionalProperties": False},
    ):
        rejected(lambda: bridge["check_schema"](schema))
    rejected(lambda: bridge["definitions"]({"tools": MANIFEST["tools"] * 2}))
    schema = {"type": "object", "properties": {"label": {"type": "string", "enum": ["a", "b"]}, "count": {"type": "integer"}}, "required": ["label"], "additionalProperties": False}
    assert bridge["normalize"](schema, {"label": "a", "count": None}) == {"label": "a"}
    rejected(lambda: bridge["normalize"](schema, {"label": "c"}))
    rejected(lambda: bridge["normalize"](schema, {"label": "a", "count": True}))

    session = bridge["create_session"](MANIFEST)
    try:
        assert session.stat().st_mode & 0o777 == 0o700
        response = {"ok": True, "result": {"stock": 37}}
        task = asyncio.create_task(bridge["exchange"](session, "lookup_host_inventory", {"code": "FM-DEMO-7"}, 3))
        request = await pending(session)
        assert request["name"] == "lookup_host_inventory" and request["arguments"] == {"code": "FM-DEMO-7"}
        rejected(lambda: bridge["reply"](session, "../arbitrary", response))
        rejected(lambda: bridge["reply"](session, "0" * 32, response))
        rejected(lambda: bridge["reply"](session, request["id"], {"ok": True}))
        bridge["reply"](session, request["id"], response)
        rejected(lambda: bridge["reply"](session, request["id"], response))
        assert await task == response
        assert not bridge["poll"](session)
        assert not bridge["session_errors"](session)
        assert not (await bridge["exchange"](session, "unregistered", {}, 1))["ok"]
        assert bridge["session_errors"](session)[0]["type"] == "BridgeError"
        error_count = len(bridge["session_errors"](session))
        assert not (await bridge["exchange"](session, "lookup_host_inventory", {"code": 123}, 1))["ok"]
        assert len(bridge["session_errors"](session)) == error_count + 1
        os.mkfifo(session / "fifo")
        rejected(lambda: bridge["read_json"](session / "fifo"))
        rejected(lambda: bridge["read_json"](session))
        bridge["write_json"](session / ("f" * 32 + ".request.json"), {"id": "f" * 32, "deadline": 0})
        assert not bridge["poll"](session)
        rejected(lambda: bridge["reply"](session, "f" * 32, response))

        task = asyncio.create_task(bridge["exchange"](session, "lookup_host_inventory", {"code": "x"}, 3))
        request = await pending(session)
        denied = {"ok": False, "errors": [{"type": "Denied", "message": "User declined access"}]}
        bridge["reply"](session, request["id"], denied)
        assert await task == denied
        assert any(error["type"] == "Denied" for error in bridge["session_errors"](session))

        task = asyncio.create_task(bridge["exchange"](session, "lookup_host_inventory", {"code": "x"}, 3))
        request = await pending(session)
        bridge["write_json"](session / (request["id"] + ".reply.json"), {"id": "wrong", **response})
        assert not (await task)["ok"]

        task = asyncio.create_task(bridge["exchange"](session, "lookup_host_inventory", {"code": "x"}, 0.2))
        request = await pending(session)
        assert (await task)["errors"][0]["type"] == "ToolTimeout"
        rejected(lambda: bridge["reply"](session, request["id"], response))
        assert any(error["type"] == "ToolTimeout" for error in bridge["session_errors"](session))
        task = asyncio.create_task(bridge["exchange"](session, "lookup_host_inventory", {"code": "x"}, 3))
        await pending(session)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert not bridge["poll"](session)
        task = asyncio.create_task(bridge["exchange"](session, "lookup_host_inventory", {"code": "x"}, 3))
        await pending(session)
        bridge["close_session"](session)
        assert (await task)["errors"][0]["type"] == "BridgeClosed"
    finally:
        bridge["close_session"](session)
    denied = {"ok": False, "errors": [{"type": "SetupRequired", "message": "Install first"}]}
    with patch.dict(sys.modules, {"fm_setup": SimpleNamespace(check_installation=lambda: denied)}):
        with patch.object(sys, "argv", [str(BRIDGE), "create", "--manifest", "unused.json"]):
            output = io.StringIO()
            with redirect_stdout(output):
                assert bridge["main"]() == 1
            assert json.loads(output.getvalue()) == denied
    print("PASS: schema/allowlist, correlation, denial, timeout, cancellation, close and install gate")


async def live():
    import apple_fm_sdk as fm
    session = bridge["create_session"](MANIFEST)
    try:
        tools = bridge["create_tools"](fm, session, 40)
        model = fm.LanguageModelSession(tools=tools, instructions="Use the inventory tool for inventory questions. Do not guess the stock.")
        task = asyncio.create_task(model.respond("Use lookup_host_inventory to check product FM-DEMO-7 and report its stock.", options=fm.GenerationOptions(sampling=fm.SamplingMode.greedy())))
        request = await pending(session)
        assert request["name"] == "lookup_host_inventory" and request["arguments"]["code"] == "FM-DEMO-7", request
        bridge["reply"](session, request["id"], {"ok": True, "result": {"stock": 37}})
        result = await asyncio.wait_for(task, 60)
        transcript = await model.transcript.to_dict()
        entries = [entry for entry in transcript["transcript"]["entries"] if entry["role"] == "tool"]
        assert any(entry["toolName"] == "lookup_host_inventory" for entry in entries), transcript
        assert "37" in result and "37" in json.dumps(entries), (result, entries)
        assert not bridge["session_errors"](session)
        class MalformedArguments:
            def to_json(self):
                return "{invalid"
        rejected_arguments = json.loads(await tools[0].call(MalformedArguments()))
        assert not rejected_arguments["ok"]
        assert bridge["session_errors"](session)[0]["type"] == "BridgeError"
        # Native conversion includes enums and nested object/array/optional fields.
        extended = {"type": "object", "properties": {"tags": {"type": "array", "items": {"type": "string", "enum": ["a", "b"]}}, "nested": MANIFEST["tools"][0]["inputSchema"]}, "required": ["tags"], "additionalProperties": False}
        bridge["check_schema"](extended)
        serialized = bridge["native_type"](fm, extended, "ExtendedArguments").generation_schema().to_dict()
        assert "enum" in json.dumps(serialized), serialized
        print("PASS: real FM callback relayed to host and transcript contains returned stock")
    finally:
        bridge["close_session"](session)


async def live_empty():
    import apple_fm_sdk as fm
    empty = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
    manifest = {"tools": [{"name": "list_host_artifacts", "description": "List the currently attached private host artifacts. This tool takes no arguments.", "inputSchema": empty}]}
    session = bridge["create_session"](manifest)
    try:
        tools = bridge["create_tools"](fm, session, 40)
        schema = tools[0].arguments_schema.to_dict()
        assert schema["type"] == "object" and schema["properties"] == {}, schema
        nested = {"type": "object", "properties": {"options": empty, "groups": {"type": "array", "items": empty}}, "required": ["options", "groups"], "additionalProperties": False}
        bridge["check_schema"](nested)
        native = bridge["native_type"](fm, nested, "NestedEmptyArguments").generation_schema().to_dict()
        assert bridge["normalize"](nested, {"options": {}, "groups": [{}]}) == {"options": {}, "groups": [{}]}
        assert native["$defs"][native["properties"]["options"]["$ref"].split("/")[-1]]["properties"] == {}, native
        assert native["$defs"][native["properties"]["groups"]["items"]["$ref"].split("/")[-1]]["properties"] == {}, native
        model = fm.LanguageModelSession(tools=tools, instructions="Always call list_host_artifacts to answer questions about current attached artifacts. Never guess.")
        task = asyncio.create_task(model.respond("Call list_host_artifacts now and report the attached artifact name.", options=fm.GenerationOptions(sampling=fm.SamplingMode.greedy())))
        request = await pending(session)
        assert request["name"] == "list_host_artifacts" and request["arguments"] == {}, request
        bridge["reply"](session, request["id"], {"ok": True, "result": {"artifacts": [{"name": "Amber test artifact"}]}})
        result = await asyncio.wait_for(task, 60)
        transcript = await model.transcript.to_dict()
        entries = [entry for entry in transcript["transcript"]["entries"] if entry["role"] == "tool"]
        assert any(entry["toolName"] == "list_host_artifacts" for entry in entries), transcript
        assert "Amber test artifact" in result and "Amber test artifact" in json.dumps(entries), (result, entries)
        assert not bridge["session_errors"](session)
        print("PASS: no-argument native callback sends {}, returns host result, preserves nested empty schemas")
    finally:
        bridge["close_session"](session)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    asyncio.run(offline())
    if args.live:
        asyncio.run(live())
        asyncio.run(live_empty())
