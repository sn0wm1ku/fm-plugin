#!/usr/bin/env python3
"""Lend selected configured MCP tools to Apple FM in one program invocation."""
import argparse
import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
import json
import os
from pathlib import Path
import re
import sys
import time

from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp.client.streamable_http import streamable_http_client
import httpx2
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

import fm_bridge as bridge
from fm_mcp import create_server
from fm_setup import check_installation

DEFAULT_CONFIG = {"mcpServers": {"claude": {"command": "claude", "args": ["mcp", "serve"], "type": "stdio"}}}


def check_policy(schema):
    if isinstance(schema, dict):
        if "$ref" in schema or "$dynamicRef" in schema or "$recursiveRef" in schema:
            raise ValueError("Tool policies must be self-contained and cannot use schema references")
        for value in schema.values():
            check_policy(value)
    elif isinstance(schema, list):
        for value in schema:
            check_policy(value)


def expand(value):
    """Expand configured environment references without invoking a shell."""
    if not isinstance(value, str):
        raise ValueError("MCP configuration values must be strings")

    def replace(match):
        name, fallback = match.group(1), match.group(2)
        if name not in os.environ and fallback is None:
            raise ValueError("Required MCP environment variable is unset: " + name)
        return os.environ.get(name, fallback)

    return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}", replace, value)


@asynccontextmanager
async def connect(config, timeout):
    kind = config.get("type", "stdio")
    if config.get("disabled"):
        raise ValueError("Selected MCP server is disabled")
    if kind == "stdio":
        command = expand(config["command"])
        arguments = config.get("args", [])
        environment = config.get("env", {})
        if not command or not isinstance(arguments, list) or not isinstance(environment, dict):
            raise ValueError("Invalid stdio MCP configuration")
        params = StdioServerParameters(command=command, args=[expand(item) for item in arguments],
            env={**os.environ, **{key: expand(value) for key, value in environment.items()}},
            cwd=expand(config["cwd"]) if "cwd" in config else None)
        async with Client(params, read_timeout_seconds=timeout, mode="legacy") as client:
            yield client
    elif kind == "http":
        headers = config.get("headers", {})
        if not isinstance(headers, dict):
            raise ValueError("MCP headers must be an object")
        async with httpx2.AsyncClient(headers={key: expand(value) for key, value in headers.items()},
                                      timeout=timeout) as http:
            async with Client(streamable_http_client(expand(config["url"]), http_client=http),
                              read_timeout_seconds=timeout, mode="legacy") as client:
                yield client
    else:
        raise ValueError("Supported MCP transports are stdio and http; selected transport is " + str(kind))


def unpack(value):
    if value.structured_content is not None:
        return value.structured_content
    return json.loads(next(item.text for item in value.content if item.type == "text"))


async def tool_reply(client, name, arguments, remaining):
    try:
        response = await client.call_tool(name, arguments, read_timeout_seconds=remaining)
        data = (response.structured_content if response.structured_content is not None else
                [item.model_dump(mode="json", by_alias=True, exclude_none=True) for item in response.content])
        value = (bridge.failure("ToolFailed", json.dumps(data, ensure_ascii=False)) if response.is_error else
                 {"ok": True, "result": data})
        if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")) > bridge.LIMIT:
            return bridge.failure("OutputTooLarge", "Tool output exceeds 256 KiB; narrow the requested data")
        return value
    except Exception as error:
        # External tool boundary: do not leak transport URLs, headers or credentials.
        return bridge.failure("ToolFailed", "MCP tool call failed (" + type(error).__name__ + ")")


async def run(config, selected, prompt, instructions="", timeout=120, policy=None):
    if not isinstance(config, dict) or not isinstance(config.get("mcpServers"), dict):
        return bridge.failure("InvalidConfig", "Expected a reviewed config containing mcpServers")
    if (not isinstance(selected, list) or not all(isinstance(item, str) for item in selected)
            or not 1 <= len(selected) <= 16 or len(set(selected)) != len(selected)):
        return bridge.failure("InvalidInput", "Select 1 to 16 unique server:tool bindings")
    if type(timeout) is not int or not 5 <= timeout <= 300:
        return bridge.failure("InvalidInput", "Timeout must be between 5 and 300 seconds")
    if not isinstance(policy, dict) or any(item not in policy for item in selected):
        return bridge.failure("PolicyRequired", "Supply an approved argument schema for every selected server:tool")
    try:
        for item in selected:
            check_policy(policy[item])
            Draft202012Validator.check_schema(policy[item])
    except (ValueError, TypeError, RecursionError, SchemaError):
        return bridge.failure("InvalidPolicy", "Tool policy must be a valid self-contained JSON Schema")
    pairs = [item.split(":", 1) for item in selected]
    if any(len(pair) != 2 or not all(pair) or pair[0] not in config["mcpServers"] for pair in pairs):
        return bridge.failure("InvalidInput", "Each selected server:tool must name a configured server")
    try:
        return await asyncio.wait_for(run_connected(config, pairs, prompt, instructions, timeout, policy), timeout)
    except asyncio.TimeoutError:
        return bridge.failure("TimeoutError", "Tool session exceeded its deadline")
    except Exception as error:
        return bridge.failure("ShimFailed", "MCP connection or tool setup failed (" + type(error).__name__ + ")")


async def run_connected(config, pairs, prompt, instructions, timeout, policy):
    deadline = time.monotonic() + timeout
    async with AsyncExitStack() as stack:
        clients = {name: await stack.enter_async_context(connect(config["mcpServers"][name], timeout))
                   for name in dict.fromkeys(pair[0] for pair in pairs)}
        catalog = {}
        for name, client in clients.items():
            cursor = None
            while True:
                page = await client.list_tools(cursor=cursor)
                catalog.update({(name, tool.name): tool for tool in page.tools})
                cursor = page.next_cursor
                if cursor is None:
                    break
        definitions = []
        bindings = {}
        for index, (server, name) in enumerate(pairs):
            tool = catalog.get((server, name))
            if tool is None:
                return bridge.failure("UnknownTool", "A selected tool was not advertised by its MCP server")
            metadata = tool.model_dump(by_alias=True).get("_meta", {}) if hasattr(tool, "model_dump") else {}
            if (metadata or {}).get("anthropic/requiresUserInteraction") is True:
                return bridge.failure("ApprovalRequired", "Selected tool requires human interaction")
            alias = name if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", name) and name not in bindings else "tool_" + str(index)
            definitions.append({"name": alias, "description": tool.description or ("Call " + name),
                                "inputSchema": tool.input_schema})
            bindings[alias] = (clients[server], name, server)
        try:
            bridge.definitions({"tools": definitions})
        except (ValueError, TypeError) as error:
            return bridge.failure("InvalidSchema", str(error))
        fm = await stack.enter_async_context(Client(create_server(), mode="legacy", read_timeout_seconds=timeout))
        value = unpack(await fm.call_tool("fm_start", {"prompt": prompt, "instructions": instructions,
            "tools": definitions, "timeout_seconds": timeout}))
        calls, seen = [], set()
        while value["status"] in ("running", "tool_requests"):
            replies = []
            for request in value.get("requests", []):
                identity = request["request_id"]
                if identity in seen:
                    return bridge.failure("DuplicateRequest", "A tool request was repeated; execution stopped")
                seen.add(identity)
                binding = bindings.get(request["name"])
                remaining = min(deadline - time.monotonic(), request["deadline"] - time.time())
                if remaining <= 0:
                    return bridge.failure("TimeoutError", "FM tool session exceeded its deadline")
                if binding is None:
                    response = bridge.failure("UnknownTool", "Tool is outside the selected bindings")
                else:
                    client, name, server = binding
                    schema = next(tool["inputSchema"] for tool in definitions if tool["name"] == request["name"])
                    arguments = bridge.normalize(schema, request["arguments"])
                    if not Draft202012Validator(policy[server + ":" + name]).is_valid(arguments):
                        response = bridge.failure("ScopeDenied", "Tool arguments exceed the approved scope")
                    else:
                        response = await tool_reply(client, name, arguments, remaining)
                    calls.append({"name": server + ":" + name, "ok": response["ok"]})
                replies.append({"request_id": identity, "response": response})
            value = unpack(await fm.call_tool("fm_continue", {"session_id": value["session_id"], "replies": replies}))
        return {**{key: item for key, item in value.items() if key not in ("tool_evidence", "next_step")},
                "tool_calls": calls}


async def execute(args):
    checked = check_installation()
    if not checked["ok"]:
        return checked
    config = bridge.read_json(args.config) if args.config else DEFAULT_CONFIG
    policy = bridge.read_json(args.policy)
    prompt = args.prompt_file.read_text(encoding="utf-8")
    instructions = args.instructions_file.read_text(encoding="utf-8") if args.instructions_file else ""
    return await run(config, args.tools, prompt, instructions, args.timeout, policy)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="Reviewed MCP config. Default: local claude mcp serve")
    parser.add_argument("--policy", type=Path, required=True, help="Approved argument JSON Schemas keyed by SERVER:TOOL")
    parser.add_argument("--tools", nargs="+", required=True, metavar="SERVER:TOOL")
    parser.add_argument("--prompt-file", type=Path, required=True)
    parser.add_argument("--instructions-file", type=Path)
    parser.add_argument("--timeout", type=int, default=120, choices=range(5, 301), metavar="5..300")
    args = parser.parse_args()
    try:
        value = asyncio.run(execute(args))
    except Exception as error:
        value = bridge.failure("ShimFailed", "MCP shim failed (" + type(error).__name__ +
            "). Check the selected config, connection/authentication, schema compatibility and deadline.")
    print(json.dumps(value, ensure_ascii=False, allow_nan=False))
    return 0 if value["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
