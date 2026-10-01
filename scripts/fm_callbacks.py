"""Execute host-reviewed script callbacks without another host model turn."""
import asyncio
import json
import math
import os
from pathlib import Path
import signal

import fm_bridge as bridge


def definitions(tools):
    if not isinstance(tools, list) or len(tools) > 32:
        raise ValueError("Supply at most 32 script tools")
    if len(json.dumps(tools, allow_nan=False).encode("utf-8")) > bridge.LIMIT:
        raise ValueError("Script tool registrations exceed 256 KiB")
    for tool in tools:
        if not isinstance(tool, dict) or set(tool) != {"name", "description", "inputSchema", "command"}:
            raise ValueError("Script tools require name, description, inputSchema and command")
        command = tool["command"]
        if (not isinstance(command, list) or not 1 <= len(command) <= 64
                or any(not isinstance(part, str) or not part or "\0" in part for part in command)
                or not Path(command[0]).is_absolute()):
            raise ValueError("command must be fixed argv with an absolute executable and 1-64 non-empty strings")
    stripped = [{key: value for key, value in tool.items() if key != "command"} for tool in tools]
    return bridge.definitions({"tools": stripped}) if stripped else []


async def bounded_read(stream):
    output = bytearray()
    while chunk := await stream.read(8192):
        output.extend(chunk)
        if len(output) > bridge.LIMIT:
            raise ValueError("Callback output exceeds 256 KiB")
    return bytes(output)


async def send_arguments(stream, encoded):
    try:
        stream.write(encoded)
        await stream.drain()
    except (BrokenPipeError, ConnectionResetError):
        # Exit status and stdout still determine whether the callback succeeded.
        pass
    finally:
        stream.close()


async def discard(stream):
    while await stream.read(8192):
        pass


async def execute(tool, arguments, timeout):
    """Send validated JSON on stdin to fixed argv, returning a bridge envelope."""
    process = None
    tasks = []
    try:
        definitions([tool])
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Callback timeout must be positive and finite")
        encoded = json.dumps(bridge.normalize(tool["inputSchema"], arguments),
                             ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(encoded) > bridge.LIMIT:
            raise ValueError("Callback arguments exceed 256 KiB")
        process = await asyncio.create_subprocess_exec(
            *tool["command"], stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            start_new_session=True)
        tasks = [asyncio.create_task(job) for job in (
            send_arguments(process.stdin, encoded), bounded_read(process.stdout),
            bounded_read(process.stderr), process.wait())]
        _, output, _, status = await asyncio.wait_for(asyncio.gather(*tasks), timeout)
        if status != 0:
            return bridge.failure("CallbackExit", "Callback exited with status " + str(status))
        value = json.loads(output)
        # Reject non-JSON numeric values accepted by Python's default decoder.
        json.dumps(value, allow_nan=False)
        return bridge.response_value(value)
    except asyncio.TimeoutError:
        return bridge.failure("CallbackTimeout", "Script callback exceeded its deadline")
    except (OSError, ValueError, TypeError, RecursionError) as error:
        return bridge.failure("CallbackError", str(error))
    finally:
        if process is not None:
            # Kill the group even if the leader exited while a child held pipes open.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if process is not None:
            # Drain bounded chunks after killing writers so full pipe buffers cannot
            # prevent asyncio from finishing the subprocess transport and reaping it.
            await asyncio.gather(discard(process.stdout), discard(process.stderr))
            await process.wait()
