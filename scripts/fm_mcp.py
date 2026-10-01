#!/usr/bin/env python3
"""Callable host-tool relay for Apple's on-device Foundation Models."""
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time
from typing import Annotated
import uuid

try:
    import anyio
    from mcp.server import MCPServer
    from mcp.types import CallToolResult, TextContent, ToolAnnotations
    from pydantic import ConfigDict, Field, with_config
    from typing_extensions import TypedDict
except ImportError as error:
    print("FM bridge startup failed: " + str(error) +
          ". Run fm:ask to check prerequisites and approve installing mcp==2.2.0 in your FM Python environment.",
          file=sys.stderr)
    raise SystemExit(1)

import fm_bridge as bridge

ToolDefinition = with_config(ConfigDict(extra="forbid", strict=True))(TypedDict("ToolDefinition", {
    "name": str, "description": str, "inputSchema": dict[str, object]}))
ToolReply = with_config(ConfigDict(extra="forbid", strict=True))(
    TypedDict("ToolReply", {"request_id": str, "response": dict[str, object]}))


def result(value):
    return CallToolResult(content=[TextContent(type="text", text=json.dumps(value, ensure_ascii=False))],
                          structuredContent=value, isError=not value["ok"])


def failed(kind, message):
    return {**bridge.failure(kind, message), "status": "failed"}


async def spawn_worker(directory, relay, instructions, timeout):
    argv = [sys.executable, str(Path(__file__).with_name("fm_sdk.py")), "respond",
            "--timeout", str(timeout), "--save-transcript", str(directory / "transcript.json")]
    if relay is not None:
        argv.extend(["--bridge", str(relay)])
    if instructions:
        (directory / "instructions.txt").write_text(instructions, encoding="utf-8")
        argv.extend(["--instructions-file", str(directory / "instructions.txt")])
    return await asyncio.create_subprocess_exec(*argv, stdin=asyncio.subprocess.PIPE,
                                               stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)


async def stop_process(process):
    if process is not None and process.returncode is None:
        try:
            process.terminate()
        except ProcessLookupError:
            await process.wait()
            return
        try:
            await asyncio.wait_for(process.wait(), 2)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()


def create_server():
    sessions = {}

    async def run(session, prompt, instructions, timeout):
        process = None
        try:
            process = await spawn_worker(session["directory"], session["relay"], instructions, timeout)
            stdout, stderr = await asyncio.wait_for(process.communicate(prompt.encode("utf-8")), timeout)
            if len(stdout) > bridge.LIMIT:
                session["outcome"] = failed("OutputTooLarge", "FM output exceeds the bridge response limit")
            else:
                if not stdout.strip():
                    raise ValueError("FM helper exited with code " + str(process.returncode) + ": " +
                                     (stderr.decode("utf-8", errors="replace")[-2000:] or "No result was returned"))
                value = json.loads(stdout)
                if not isinstance(value, dict) or type(value.get("ok")) is not bool:
                    raise ValueError("FM helper returned an invalid result")
                if process.returncode != 0 and value["ok"]:
                    value = bridge.failure("WorkerFailed", "FM helper exited unsuccessfully")
                evidence = []
                transcript = session["directory"] / "transcript.json"
                if transcript.exists():
                    entries = bridge.read_json(transcript)["transcript"]["entries"]
                    evidence = [{"name": entry["toolName"], "output": entry.get("contents", [])}
                                for entry in entries if entry.get("role") == "tool"]
                session["outcome"] = {**value, "status": "completed" if value["ok"] else "failed",
                                      "tool_evidence": evidence}
        except asyncio.TimeoutError:
            session["outcome"] = failed("TimeoutError", "FM session exceeded its deadline")
        except asyncio.CancelledError:
            session["outcome"] = failed("Cancelled", "FM session cancelled")
        except (OSError, ValueError, KeyError, TypeError) as error:
            session["outcome"] = failed("BridgeError", str(error))
        finally:
            await stop_process(process)
            if session["relay"] is not None:
                bridge.close_session(session["relay"])
            shutil.rmtree(session["directory"])

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            with anyio.CancelScope(shield=True):
                tasks = [session["task"] for session in sessions.values() if not session["task"].done()]
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    server = MCPServer("fm", instructions=(
        "YOU, the host assistant (Codex or Claude), must supply tools Apple FM needs. "
        "For current/private information, discover your own available tools and pass their reviewed "
        "name, description and inputSchema to fm_start. Execute returned tool_requests yourself with "
        "your normal permissions, then give real results to fm_continue. FM cannot invoke your tools "
        "without this relay. Use an empty tools array only for tasks answerable from supplied text."),
        lifespan=lifespan)

    async def snapshot(identifier, session):
        # Return within one second so the host remains free to service FM's calls.
        for _ in range(10):
            if session["task"].done():
                return result({**session["outcome"], "session_id": identifier})
            try:
                requests = bridge.poll(session["relay"]) if session["relay"] is not None else []
            except FileNotFoundError:
                requests = []  # fm_sdk removes the relay immediately before exiting.
            if requests:
                return result({"ok": True, "status": "tool_requests", "session_id": identifier,
                    "requests": [{"request_id": item["id"], "name": item["name"],
                                  "arguments": item["arguments"], "deadline": min(item["deadline"], session["deadline"])}
                                 for item in requests],
                    "next_step": "HOST: execute each request_id at most once using its registered host tool and normal permissions. Call fm_continue with the actual result or error. Never invent results or repeat a side effect when polling returns the same request_id."})
            await asyncio.sleep(0.1)
        return result({"ok": True, "status": "running", "session_id": identifier,
                       "next_step": "Call fm_continue with replies:[] to poll for tool requests or the final result."})

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False), description=(
        "Start Apple FM with tools YOU (Codex/Claude) supply. For live/current/private data, FIRST "
        "discover your available host tools, review their schemas, then pass the selected tools here. "
        "This registers tools with FM, but YOU must execute returned tool_requests and submit their "
        "real results through fm_continue. tools:[] explicitly selects a self-contained text task. "
        "Keep alias-to-host-tool mapping yourself. Setup/license checks run before inference. "
        "Returns running, tool_requests, completed, or failed; keep calling fm_continue until terminal."))
    async def fm_start(
        prompt: Annotated[str, Field(strict=True, min_length=1, max_length=65536)],
        tools: Annotated[list[ToolDefinition], Field(max_length=16)],
        instructions: Annotated[str, Field(strict=True, max_length=16384)] = "",
        timeout_seconds: Annotated[int, Field(strict=True, ge=5, le=300)] = 120,
    ) -> CallToolResult:
        if not prompt.strip():
            return result(failed("InvalidInput", "Prompt must contain text"))
        try:
            if tools:
                bridge.definitions({"tools": tools})
            if len(json.dumps(tools, allow_nan=False).encode("utf-8")) > bridge.LIMIT:
                return result(failed("InvalidInput", "Tool definitions exceed 256 KiB"))
        except (ValueError, TypeError) as error:
            return result(failed("InvalidInput", str(error)))
        if sum(not session["task"].done() for session in sessions.values()) >= 4:
            return result(failed("SessionLimit", "Four FM sessions are active. Finish or cancel one first."))
        for identifier in [key for key, session in sessions.items() if session["task"].done()][:-15]:
            del sessions[identifier]
        directory = relay = None
        try:
            directory = Path(tempfile.mkdtemp(prefix="fm-mcp-"))
            relay = bridge.create_session({"tools": tools}) if tools else None
        except (OSError, ValueError) as error:
            if directory is not None:
                shutil.rmtree(directory)
            return result(failed("BridgeError", str(error)))
        identifier = uuid.uuid4().hex
        session = {"directory": directory, "relay": relay, "replied": set(),
                   "deadline": time.time() + timeout_seconds}
        session["task"] = asyncio.create_task(run(session, prompt, instructions, timeout_seconds))
        sessions[identifier] = session
        return await snapshot(identifier, session)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False), description=(
        "Continue an FM session. YOU must execute each returned tool_request with the corresponding "
        "registered host tool, then submit {request_id,response:{ok:true,result:ACTUAL_OUTPUT}} or "
        "{request_id,response:{ok:false,errors:[{type,message}]}}. Never fabricate results or repeat "
        "host side effects. Use replies:[] to poll. Duplicate, unknown and expired replies are rejected. "
        "Continue until completed or failed; do not abandon a running session."))
    async def fm_continue(
        session_id: Annotated[str, Field(strict=True, pattern="^[0-9a-f]{32}$")],
        replies: Annotated[list[ToolReply], Field(max_length=16)],
    ) -> CallToolResult:
        session = sessions.get(session_id)
        if session is None:
            return result(failed("UnknownSession", "Session is unknown or its retained result expired"))
        if replies:
            if session["task"].done() or session["deadline"] <= time.time() or session["relay"] is None:
                return result(failed("ExpiredSession", "Session no longer accepts tool replies"))
            try:
                pending = {item["id"]: item for item in bridge.poll(session["relay"])}
                identifiers = [item["request_id"] for item in replies]
                if len(set(identifiers)) != len(identifiers) or any(
                        key in session["replied"] or key not in pending for key in identifiers):
                    return result(failed("InvalidReply", "Duplicate, unknown or expired request_id. Do not execute the host tool again."))
                # Validate the whole batch before any reply becomes visible to FM.
                for item in replies:
                    bridge.response_value(item["response"])
                    encoded = json.dumps({"id": item["request_id"], **item["response"]},
                                         ensure_ascii=False, allow_nan=False).encode("utf-8")
                    if len(encoded) > bridge.LIMIT:
                        raise ValueError("Reply exceeds 256 KiB; provide a smaller tool result")
                for item in replies:
                    bridge.reply(session["relay"], item["request_id"], item["response"])
                    session["replied"].add(item["request_id"])
            except (OSError, ValueError, TypeError) as error:
                return result(failed("InvalidReply", str(error)))
        return await snapshot(session_id, session)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False), description="Cancel an FM session and clean its subprocess and private relay files. Do not execute further host tool requests for this session.")
    async def fm_cancel(session_id: Annotated[str, Field(strict=True, pattern="^[0-9a-f]{32}$")]) -> CallToolResult:
        session = sessions.get(session_id)
        if session is None:
            return result(failed("UnknownSession", "Session is unknown or its retained result expired"))
        if not session["task"].done():
            session["task"].cancel()
            await session["task"]
        return result({**session["outcome"], "session_id": session_id})

    return server


if __name__ == "__main__":
    create_server().run(transport="stdio")
