#!/usr/bin/env python3
"""Relay selected host tools to Foundation Models through private JSON files."""
import argparse
import asyncio
import json
import keyword
import math
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import time
from typing import Optional
import uuid

LIMIT = 256 * 1024


def failure(kind, message):
    return {"ok": False, "errors": [{"type": kind, "message": message}]}


def read_json(path):
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > LIMIT:
        raise ValueError("Bridge files must be regular, bounded JSON files")
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value):
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(encoded) > LIMIT:
        raise ValueError("Bridge JSON exceeds 256 KiB; provide a smaller tool result")
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as target:
        temporary = Path(target.name)
        try:
            target.write(encoded)
            target.flush()
            os.fsync(target.fileno())
            # Publish atomically without replacing a previous reply/request.
            os.link(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def check_schema(schema, root=True):
    if not isinstance(schema, dict):
        raise ValueError("Each inputSchema must be a JSON object")
    kind = schema.get("type")
    allowed = {"type", "description", "title"}
    allowed |= {"properties", "required", "additionalProperties"} if kind == "object" else {"items"} if kind == "array" else {"enum"} if kind == "string" else set()
    if kind not in ("object", "array", "string", "number", "integer", "boolean") or set(schema) - allowed:
        raise ValueError("Unsupported bridge schema type or keyword: " + str(kind) + " " + str(sorted(set(schema) - allowed)))
    if root and kind != "object":
        raise ValueError("Tool inputSchema must describe an object")
    if any(not isinstance(schema[key], str) for key in ("description", "title") if key in schema):
        raise ValueError("Schema title and description must be strings")
    if kind == "object":
        props, required = schema.get("properties"), schema.get("required", [])
        if schema.get("additionalProperties") is not False or not isinstance(props, dict):
            raise ValueError("Object schemas require properties and additionalProperties:false")
        if not isinstance(required, list) or not all(isinstance(key, str) for key in required) or not set(required) <= set(props):
            raise ValueError("required must list defined property names")
        for key, child in props.items():
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", key) or keyword.iskeyword(key):
                raise ValueError("Property names must be Python-compatible identifiers")
            check_schema(child, False)
    if kind == "array":
        check_schema(schema.get("items"), False)
    if "enum" in schema and (not isinstance(schema["enum"], list) or not schema["enum"] or not all(isinstance(item, str) for item in schema["enum"])):
        raise ValueError("enum must be a non-empty list of strings")


def definitions(manifest):
    if not isinstance(manifest, dict) or set(manifest) != {"tools"} or not isinstance(manifest["tools"], list) or not manifest["tools"]:
        raise ValueError("Manifest must contain a non-empty tools array")
    names = []
    for item in manifest["tools"]:
        if not isinstance(item, dict) or set(item) != {"name", "description", "inputSchema"}:
            raise ValueError("Each tool needs exactly name, description and inputSchema")
        if not isinstance(item["name"], str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", item["name"]):
            raise ValueError("Tool names must be identifiers of at most 64 characters")
        if not isinstance(item["description"], str) or not item["description"].strip() or item["name"] in names:
            raise ValueError("Tool descriptions must be non-empty and names unique")
        names.append(item["name"])
        check_schema(item["inputSchema"])
    return manifest["tools"]


def session_path(session):
    path = Path(session)
    metadata = path.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o700 or not path.name.startswith("fm-bridge-"):
        raise ValueError("Expected an owned, private fm-bridge session directory")
    if read_json(path / "session.json") != {"protocol": 1}:
        raise ValueError("Unsupported bridge session")
    return path


def create_session(manifest):
    definitions(manifest)
    path = Path(tempfile.mkdtemp(prefix="fm-bridge-"))
    try:
        write_json(path / "session.json", {"protocol": 1})
        write_json(path / "manifest.json", manifest)
        return path
    except (OSError, ValueError):
        shutil.rmtree(path)
        raise


def close_session(session):
    if Path(session).exists():
        shutil.rmtree(session_path(session))


def normalize(schema, value):
    kind = schema["type"]
    if kind == "object":
        props, required = schema["properties"], schema.get("required", [])
        if not isinstance(value, dict) or set(value) - set(props) or not set(required) <= set(value):
            raise ValueError("Tool arguments do not match the registered object schema")
        # The SDK emits null for absent Optional fields; MCP represents omission.
        return {key: normalize(props[key], item) for key, item in value.items()
                if item is not None or key in required}
    if kind == "array":
        if not isinstance(value, list):
            raise ValueError("Expected array tool argument")
        return [normalize(schema["items"], item) for item in value]
    valid = (isinstance(value, str) if kind == "string" else type(value) is bool if kind == "boolean" else
             type(value) is int if kind == "integer" else type(value) in (int, float) and math.isfinite(value))
    if not valid or "enum" in schema and value not in schema["enum"]:
        raise ValueError("Tool argument violates type or enum")
    return value


def response_value(value):
    if not isinstance(value, dict) or type(value.get("ok")) is not bool:
        raise ValueError("Reply requires a boolean ok")
    if value["ok"]:
        if set(value) != {"ok", "result"}:
            raise ValueError("Successful reply requires only ok and result")
    elif set(value) != {"ok", "errors"} or not isinstance(value["errors"], list) or not value["errors"] or any(
            not isinstance(error, dict) or set(error) != {"type", "message"} or
            not all(isinstance(error[key], str) for key in ("type", "message")) for error in value["errors"]):
        raise ValueError("Failed reply requires errors:[{type,message}]")
    return value


def poll(session):
    path = session_path(session)
    requests = [read_json(request) for request in sorted(path.glob("*.request.json"))
                if not request.with_name(request.name.replace(".request.", ".reply.")).exists()]
    return [request for request in requests if request["deadline"] > time.time()]


def session_errors(session):
    path = session_path(session)
    return [error for file in sorted(path.glob("*.error.json")) for error in read_json(file)["errors"]]


def reply(session, request_id, response):
    path = session_path(session)
    if not re.fullmatch(r"[0-9a-f]{32}", request_id):
        raise ValueError("Invalid request id")
    request = read_json(path / (request_id + ".request.json"))
    if request["id"] != request_id or request["deadline"] <= time.time():
        raise ValueError("Request expired or id mismatched")
    write_json(path / (request_id + ".reply.json"), {"id": request_id, **response_value(response)})


async def exchange(session, tool, arguments, timeout):
    request = response = None
    path = None
    identifier = uuid.uuid4().hex
    try:
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Tool timeout must be positive and finite")
        path = session_path(session)
        allowed = definitions(read_json(path / "manifest.json"))
        registered = next((entry for entry in allowed if entry["name"] == tool), None)
        if registered is None:
            raise ValueError("Tool is not in the bridge allowlist")
        arguments = normalize(registered["inputSchema"], arguments)
        request, response = path / (identifier + ".request.json"), path / (identifier + ".reply.json")
        write_json(request, {"id": identifier, "name": tool, "arguments": arguments, "deadline": time.time() + timeout})
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not path.exists():
                return failure("BridgeClosed", "The host closed this tool relay")
            if response.exists():
                value = read_json(response)
                if value.pop("id", None) != identifier:
                    raise ValueError("Reply id does not match the pending tool call")
                result = response_value(value)
                if not result["ok"]:
                    write_json(path / (identifier + ".error.json"), result)
                return result
            await asyncio.sleep(0.1)
        result = failure("ToolTimeout", "Host did not reply before the tool deadline")
        write_json(path / (identifier + ".error.json"), result)
        return result
    except (OSError, ValueError, TypeError) as error:
        result = failure("BridgeError", str(error))
        if path is not None and path.exists():
            write_json(path / (identifier + ".error.json"), result)
        return result
    finally:
        for file in (request, response):
            if file is not None:
                file.unlink(missing_ok=True)


def native_type(fm, schema, name):
    kind = schema["type"]
    if kind == "array":
        return list[native_type(fm, schema["items"], name + "Item")]
    if kind != "object":
        return {"string": str, "number": float, "integer": int, "boolean": bool}[kind]
    annotations = {key: native_type(fm, child, name + key) for key, child in schema["properties"].items()}
    annotations = {key: value if key in schema.get("required", []) else Optional[value] for key, value in annotations.items()}
    fields = {key: fm.guide(child.get("description"), **native_guides(fm, child))
              for key, child in schema["properties"].items()}
    return fm.generable(schema.get("description", name))(type(name, (), {"__annotations__": annotations, **fields}))


def native_guides(fm, schema):
    if "enum" in schema:
        return {"anyOf": schema["enum"]}
    if schema["type"] == "array":
        child = native_guides(fm, schema["items"])
        guide = fm.GenerationGuide.anyOf(child["anyOf"]) if "anyOf" in child else child.get("element")
        if guide is not None:
            return {"element": guide if "anyOf" in child else fm.GenerationGuide.element(guide)}
    return {}


def create_tools(fm, session, timeout=120):
    path = session_path(session)

    def make_tool(definition):
        schema = native_type(fm, definition["inputSchema"], definition["name"] + "Arguments").generation_schema()

        async def call(self, args):
            try:
                arguments = json.loads(args.to_json())
            except (fm.FoundationModelsError, ValueError, TypeError) as error:
                result = failure("BridgeError", "Could not decode native tool arguments: " + str(error))
                if path.exists():
                    write_json(path / (uuid.uuid4().hex + ".error.json"), result)
            else:
                result = await exchange(path, definition["name"], arguments, timeout)
            return json.dumps(result, ensure_ascii=False, allow_nan=False)

        return type("HostTool", (fm.Tool,), {"name": definition["name"], "description": definition["description"],
                    "arguments_schema": property(lambda self: schema), "call": call})()

    return [make_tool(item) for item in definitions(read_json(path / "manifest.json"))]


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    commands = cli.add_subparsers(dest="command", required=True)
    commands.add_parser("create").add_argument("--manifest", type=Path, required=True)
    for name in ("poll", "reply", "close"):
        sub = commands.add_parser(name)
        sub.add_argument("--session", type=Path, required=True)
        if name == "reply":
            sub.add_argument("--id", required=True)
            sub.add_argument("--response", type=Path, required=True)
    args = cli.parse_args()
    try:
        if args.command == "create":
            from fm_setup import check_installation
            checked = check_installation()
            if not checked["ok"]:
                print(json.dumps(checked))
                return 1
            value = {"ok": True, "session": str(create_session(read_json(args.manifest)))}
        elif args.command == "poll":
            value = {"ok": True, "requests": poll(args.session)}
        elif args.command == "reply":
            reply(args.session, args.id, read_json(args.response))
            value = {"ok": True}
        else:
            close_session(args.session)
            value = {"ok": True}
    except (OSError, ValueError, TypeError) as error:
        value = failure("BridgeError", str(error))
    print(json.dumps(value, ensure_ascii=False, allow_nan=False))
    return 0 if value["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
