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
import fm_callbacks as callbacks

ToolDefinition = with_config(ConfigDict(extra="forbid", strict=True))(TypedDict("ToolDefinition", {
    "name": str, "description": str, "inputSchema": dict[str, object]}))
ToolReply = with_config(ConfigDict(extra="forbid", strict=True))(
    TypedDict("ToolReply", {"request_id": str, "response": dict[str, object]}))
ScriptTool = with_config(ConfigDict(extra="forbid", strict=True))(TypedDict("ScriptTool", {
    "name": str, "description": str, "inputSchema": dict[str, object], "command": list[str]}))


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
            with anyio.CancelScope(shield=True):
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
        "Prepare context and lend selected tools to Apple FM through a PROGRAMMATIC shim. "
        "Prepare a self-contained task from the relevant conversation, authorized source material, "
        "confirmed facts, constraints and desired output. Supply it in fm_start.prompt and instructions. "
        "For each execution, select needed authorized host tools and supply their reviewed name, "
        "description and inputSchema in fm_start.tools. FM receives the context and tools you pass. "
        "Run scripts/fm_host.js inside the host tool runtime, binding actual host callables once. "
        "The program executes tool_requests and feeds results through fm_continue automatically; "
        "do not use host LLM turns to service callbacks or polls. For reviewed executable script "
        "bindings, use fm_run which manages the loop internally. Use tools:[] for supplied-context tasks. "
        "Check FM's final answer against the original task before replying to the user."),
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
                    "next_step": "SHIM: dispatch each request_id at most once to its registered host callable and return the actual result through fm_continue. Keep this loop in code, without host LLM turns."})
            await asyncio.sleep(0.1)
        return result({"ok": True, "status": "running", "session_id": identifier,
                       "next_step": "SHIM: poll fm_continue with replies:[] inside the same program until completion."})

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False), description=(
        "Low-level start for the programmatic host-tool shim. Prepare context and callable bindings once. Assemble "
        "a self-contained prompt from the relevant conversation, source material, facts, constraints "
        "and requested output; pass task guidance in instructions. Supply selected authorized host "
        "tool definitions in tools for THIS execution. FM sees only the context and tools supplied. "
        "Use scripts/fm_host.js in the host tool runtime to execute requests and submit real results "
        "through fm_continue in code. Do not involve the host LLM in individual callbacks or polling. "
        "Use tools:[] only when supplied context is sufficient. "
        "Keep alias-to-host-tool callable mapping in the program. Setup/license checks run before inference. "
        "Returns running, tool_requests, completed, or failed; keep calling fm_continue until terminal."))
    async def fm_start(
        prompt: Annotated[str, Field(strict=True, min_length=1, max_length=65536,
            description="Host-prepared, self-contained task with relevant conversation context, authorized source material, confirmed facts and desired output. Include needed prior context explicitly.")],
        tools: Annotated[list[ToolDefinition], Field(max_length=16,
            description="Reviewed definitions for this execution. The programmatic shim retains actual host callables and dispatches FM's requests. Use [] when supplied context suffices.")],
        instructions: Annotated[str, Field(strict=True, max_length=16384,
            description="Host-prepared task guidance, constraints and output format for FM.")] = "",
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
        try:
            return await snapshot(identifier, session)
        except asyncio.CancelledError:
            with anyio.CancelScope(shield=True):
                session["task"].cancel()
                await session["task"]
            raise

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True), description=(
        "Run Apple FM with reviewed executable tool bindings and return only the final result. "
        "Prepare context and fixed script argv once. The program validates FM arguments, sends JSON "
        "to each registered program's stdin, and feeds its JSON response back to FM automatically. "
        "No host LLM callback turns or polling are needed. Scripts must already have an authorized "
        "programmatic connection to the selected tools; host-only tool names are not executable commands. "
        "For live host callable references instead, run scripts/fm_host.js in the host tool runtime. "
        "Commands are trusted executable code: review scope before registering; never run FM-generated code."))
    async def fm_run(
        prompt: Annotated[str, Field(strict=True, min_length=1, max_length=65536)],
        tools: Annotated[list[ScriptTool], Field(max_length=16,
            description="Reviewed definitions plus fixed command argv with an absolute executable. JSON arguments go to stdin, not shell interpolation. Program stdout must be {ok:true,result:...} or {ok:false,errors:[{type,message}]}. Use [] for self-contained tasks.")],
        instructions: Annotated[str, Field(strict=True, max_length=16384)] = "",
        timeout_seconds: Annotated[int, Field(strict=True, ge=5, le=300)] = 120,
    ) -> CallToolResult:
        try:
            definitions = callbacks.definitions(tools)
        except (ValueError, TypeError) as error:
            return result(failed("InvalidInput", str(error)))
        started = await fm_start(prompt, definitions, instructions, timeout_seconds)
        value = json.loads(started.content[0].text)
        if "session_id" not in value:
            return started
        identifier = value["session_id"]
        session = sessions[identifier]
        bindings = {tool["name"]: tool for tool in tools}
        calls = []
        try:
            while not session["task"].done():
                requests = value.get("requests", [])
                replies = []
                for request in requests:
                    remaining = min(request["deadline"], session["deadline"]) - time.time()
                    if remaining <= 0:
                        break
                    tool = bindings.get(request["name"])
                    response = (await callbacks.execute(tool, request["arguments"], remaining)
                                if tool is not None else bridge.failure("UnknownTool", "Tool is not registered"))
                    calls.append({"name": request["name"], "ok": response["ok"]})
                    replies.append({"request_id": request["request_id"], "response": response})
                if session["task"].done():
                    break
                continued = await fm_continue(identifier, replies)
                value = json.loads(continued.content[0].text)
                if not value["ok"]:
                    return result({key: item for key, item in value.items() if key != "tool_evidence"})
            await session["task"]
            return result({**{key: item for key, item in session["outcome"].items() if key != "tool_evidence"},
                           "session_id": identifier, "tool_calls": calls})
        finally:
            with anyio.CancelScope(shield=True):
                if not session["task"].done():
                    session["task"].cancel()
                    await session["task"]

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False), description=(
        "Low-level continuation for the programmatic shim, not an instruction for LLM babysitting. "
        "The program executes each returned tool_request with the corresponding "
        "registered host tool, then submit {request_id,response:{ok:true,result:ACTUAL_OUTPUT}} or "
        "{request_id,response:{ok:false,errors:[{type,message}]}}. Never fabricate results or repeat "
        "host side effects. Use replies:[] to poll. Duplicate, unknown and expired replies are rejected. "
        "Continue until completed or failed; verify the final answer against the original task and context."))
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
