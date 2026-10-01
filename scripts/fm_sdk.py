#!/usr/bin/env python3
"""Command-line access to Apple's on-device Foundation Models SDK."""
import argparse
import asyncio
import contextlib
import importlib
import importlib.metadata
import json
import math
import multiprocessing
import os
from pathlib import Path
import runpy
import sys
import tempfile


def failure(kind, message):
    return {"ok": False, "errors": [{"type": kind, "message": message}]}


def emit(value, stream=sys.stdout):
    print(json.dumps(value, ensure_ascii=False, allow_nan=False), file=stream, flush=True)


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    commands = cli.add_subparsers(dest="command", required=True)
    for command in ("available", "tokens", "respond", "batch"):
        sub = commands.add_parser(command)
        sub.add_argument("--use-case", choices=("general", "content-tagging"), default="general")
        sub.add_argument("--guardrails", choices=("default", "permissive-transformations"), default="default")
        if command == "available":
            continue
        sub.add_argument("input", nargs="?", help="Prompt, or JSONL filename for batch; omitted reads stdin")
        instructions = sub.add_mutually_exclusive_group()
        instructions.add_argument("--instructions")
        instructions.add_argument("--instructions-file", type=Path)
        sub.add_argument("--schema", type=Path)
        sub.add_argument("--extension", type=Path, help="Execute trusted Python exporting create_tools() and/or Output")
        sub.add_argument("--timeout", type=float, default=120, help="Seconds per request (default: 120)")
        if command != "batch":
            sub.add_argument("--image", type=Path, action="append", default=[])
            sub.add_argument("--resume", type=Path)
        if command == "tokens":
            continue
        sampling = sub.add_mutually_exclusive_group()
        sampling.add_argument("--greedy", action="store_true")
        sampling.add_argument("--top-k", type=int)
        sampling.add_argument("--top-p", type=float)
        sub.add_argument("--seed", type=int)
        sub.add_argument("--temperature", type=float)
        sub.add_argument("--max-tokens", type=int)
        if command == "respond":
            sub.add_argument("--stream", action="store_true", help="JSONL full-text snapshots, then final result")
            sub.add_argument("--save-transcript", type=Path)
        else:
            sub.add_argument("--continue-on-error", action="store_true")
    return cli


def validate(args):
    checks = (
        (getattr(args, "timeout", 1) <= 0 or not math.isfinite(getattr(args, "timeout", 1)), "timeout must be finite and positive"),
        (getattr(args, "top_k", None) is not None and args.top_k <= 0, "top-k must be positive"),
        (getattr(args, "top_p", None) is not None and not 0 <= args.top_p <= 1, "top-p must be between 0 and 1"),
        (getattr(args, "temperature", None) is not None and (not math.isfinite(args.temperature) or args.temperature < 0), "temperature must be finite and non-negative"),
        (getattr(args, "max_tokens", None) is not None and args.max_tokens <= 0, "max-tokens must be positive"),
        (getattr(args, "seed", None) is not None and (args.seed < 0 or args.seed >= 2**64), "seed must fit an unsigned 64-bit integer"),
        (getattr(args, "greedy", False) and getattr(args, "seed", None) is not None, "greedy cannot be combined with seed"),
        (getattr(args, "seed", None) is not None and getattr(args, "top_k", None) is None and getattr(args, "top_p", None) is None, "seed requires top-k or top-p on SDK 0.2.1"),
        (getattr(args, "stream", False) and getattr(args, "schema", None) is not None, "stream supports text only; remove schema"),
        (getattr(args, "resume", None) is not None and (args.instructions is not None or args.instructions_file is not None), "resume uses saved instructions; omit new instructions"),
    )
    errors = [{"type": "InvalidInput", "message": message} for invalid, message in checks if invalid]
    return {"ok": False, "errors": errors} if errors else {"ok": True}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def save_transcript(path, data):
    """Atomic replacement preserves an existing conversation on write failure."""
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as target:
        temporary = Path(target.name)
        try:
            json.dump(data, target, ensure_ascii=False, allow_nan=False)
            target.flush()
            os.fsync(target.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def configuration(args, fm):
    extension = runpy.run_path(str(args.extension.resolve())) if args.extension else {}
    tools = extension["create_tools"]() if "create_tools" in extension else []
    if not isinstance(tools, list) or not all(isinstance(tool, fm.Tool) for tool in tools):
        return failure("InvalidExtension", "create_tools() must return a list of fm.Tool instances")
    output = extension.get("Output")
    if output is not None and not isinstance(output, fm.Generable):
        return failure("InvalidExtension", "Output must be an @fm.generable type")
    if args.schema and output is not None:
        return failure("InvalidInput", "Choose either --schema or extension Output")
    if output is not None and getattr(args, "stream", False):
        return failure("InvalidInput", "stream supports text only; remove extension Output")
    schema = read_json(args.schema) if args.schema else None
    if args.schema is not None and not isinstance(schema, dict):
        return failure("InvalidInput", "schema must be a JSON object supported by Foundation Models")
    instructions = args.instructions_file.read_text(encoding="utf-8") if args.instructions_file else args.instructions
    # SDK 0.2.1 drops options on generating=; schema= preserves the same constraints.
    return {"ok": True, "tools": tools, "instructions": instructions,
            "schema": output.generation_schema() if output is not None else None,
            "json_schema": schema}


async def session_for(args, config, model, fm):
    if getattr(args, "resume", None):
        transcript = await fm.Transcript.from_dict(read_json(args.resume))
        return fm.LanguageModelSession.from_transcript(transcript, model=model, tools=config["tools"])
    return fm.LanguageModelSession(instructions=config["instructions"], model=model, tools=config["tools"])


def generation_options(args, fm):
    sampling = (
        fm.SamplingMode.greedy() if args.greedy else
        fm.SamplingMode.random(top=args.top_k, probability_threshold=args.top_p, seed=args.seed)
        if any(value is not None for value in (args.top_k, args.top_p, args.seed)) else None
    )
    return fm.GenerationOptions(sampling=sampling, temperature=args.temperature,
                                maximum_response_tokens=args.max_tokens)


def prompt_value(text, images, fm):
    if not isinstance(text, str) or not text.strip():
        return failure("InvalidInput", "prompt must be a non-empty string")
    if not isinstance(images, list) or not all(isinstance(path, (str, Path)) for path in images):
        return failure("InvalidInput", "images must be an array of file paths")
    paths = [Path(path).expanduser().resolve() for path in images]
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        return failure("InvalidInput", "Image files not found: " + ", ".join(missing))
    return {"ok": True, "value": [text, *(fm.ImageAttachment(path=path) for path in paths)] if paths else text}


async def request(args, config, model, fm, text, images):
    prompt = prompt_value(text, images, fm)
    if not prompt["ok"]:
        return prompt
    session = await session_for(args, config, model, fm)
    if args.command == "tokens":
        return {"ok": True, "context_size": model.context_size,
                "prompt_tokens": await model.token_count(prompt["value"]),
                "transcript_tokens": await model.token_count(session.transcript),
                "schema_tokens": await model.token_count(config["schema"]) if config["schema"] is not None else (None if config["json_schema"] is not None else 0),
                "note": "Transcript includes instructions, tools and saved history. Counts are components, not guaranteed total request usage; raw JSON schema counting is unsupported."}
    options = generation_options(args, fm)
    if getattr(args, "stream", False):
        result = ""
        async for snapshot in session.stream_response(prompt["value"], options=options):
            result = snapshot
            emit({"event": "snapshot", "text": snapshot})
    else:
        response = await session.respond(prompt["value"], schema=config["schema"],
                                         json_schema=config["json_schema"], options=options)
        result = json.loads(response.to_json()) if isinstance(response, fm.GeneratedContent) else response
    if isinstance(result, str) and not result.strip():
        return failure("EmptyResponse", "The model returned no text")
    if getattr(args, "save_transcript", None):
        save_transcript(args.save_transcript, await session.transcript.to_dict())
    return {"ok": True, "result": result}


async def attempt(args, config, model, fm, text, images):
    try:
        return await asyncio.wait_for(request(args, config, model, fm, text, images), timeout=args.timeout)
    except (fm.FoundationModelsError, OSError, ValueError, TypeError, asyncio.TimeoutError) as error:
        message = f"Request exceeded {args.timeout:g} seconds" if isinstance(error, (TimeoutError, asyncio.TimeoutError)) else str(error)
        return failure(type(error).__name__, message)


async def batch(args, config, model, fm):
    source = open(args.input, encoding="utf-8") if args.input else contextlib.nullcontext(sys.stdin)
    failed = False
    with source as records:
        for index, line in enumerate(records, 1):
            try:
                row = json.loads(line)
            except ValueError as error:
                row = None
                result = failure("InvalidInput", str(error))
            else:
                result = (await attempt(args, config, model, fm, row.get("prompt"), row.get("images", []))
                          if isinstance(row, dict) else failure("InvalidInput", "Each JSONL record must be an object"))
            emit({**result, "index": index, **({"id": row["id"]} if isinstance(row, dict) and "id" in row else {})})
            if not result["ok"]:
                failed = True
                if not args.continue_on_error:
                    return 1
    return int(failed)


async def dispatch(args, fm):
    model = fm.SystemLanguageModel(
        use_case=fm.SystemLanguageModelUseCase.CONTENT_TAGGING if args.use_case == "content-tagging" else fm.SystemLanguageModelUseCase.GENERAL,
        guardrails=fm.SystemLanguageModelGuardrails.PERMISSIVE_CONTENT_TRANSFORMATIONS if args.guardrails == "permissive-transformations" else fm.SystemLanguageModelGuardrails.DEFAULT)
    available, reason = model.is_available()
    if args.command == "available":
        emit({"ok": available, "available": available, "reason": reason.name if reason is not None else None,
              "sdk_version": importlib.metadata.version("apple-fm-sdk"),
              "context_size": model.context_size if available else None})
        return 0 if available else 1
    if not available:
        emit(failure("ModelUnavailable", reason.name if reason is not None else "Unknown reason"))
        return 1
    config = configuration(args, fm)
    if not config["ok"]:
        emit(config)
        return 2
    if args.command == "batch":
        return await batch(args, config, model, fm)
    text = args.input if args.input is not None else sys.stdin.read()
    if getattr(args, "save_transcript", None):
        inputs = [args.schema, args.extension, args.instructions_file, *args.image]
        if any(path and path.resolve() == args.save_transcript.resolve() for path in inputs):
            emit(failure("InvalidInput", "Transcript output must not overwrite an input file"))
            return 2
    result = await attempt(args, config, model, fm, text, args.image)
    emit({**result, **({"event": "complete" if result["ok"] else "error"} if getattr(args, "stream", False) else {})})
    return 0 if result["ok"] else 1


def execute(args):
    try:
        fm = importlib.import_module("apple_fm_sdk")
    except (ImportError, OSError) as error:
        emit(failure("SDKUnavailable", f"{error}. Install apple-fm-sdk==0.2.1 using this Python interpreter and complete Apple's setup requirements."))
        return 1
    try:
        return asyncio.run(dispatch(args, fm))
    except (fm.FoundationModelsError, OSError, ValueError, TypeError) as error:
        emit(failure(type(error).__name__, str(error)))
        return 1
    except KeyboardInterrupt:
        emit(failure("Cancelled", "Request interrupted"), sys.stderr)
        return 130
    except Exception as error:
        emit(failure("UnexpectedError", str(error)), sys.stderr)
        raise


def stream_worker(args):
    sys.exit(execute(args))


def main():
    args = parser().parse_args()
    checked = validate(args)
    if not checked["ok"]:
        emit(checked)
        return 2
    if not getattr(args, "stream", False):
        return execute(args)
    # SDK 0.2.1 blocks its event loop while waiting for snapshots. A process
    # deadline also covers the period before the first snapshot arrives.
    process = multiprocessing.get_context("spawn").Process(target=stream_worker, args=(args,))
    process.start()
    try:
        process.join(args.timeout)
        if process.is_alive():
            emit({**failure("TimeoutError", f"Request exceeded {args.timeout:g} seconds"), "event": "error"})
            return 1
        if process.exitcode < 0:
            emit({**failure("WorkerError", f"Streaming worker exited with code {process.exitcode}"), "event": "error"})
            return 1
        return process.exitcode
    except KeyboardInterrupt:
        emit(failure("Cancelled", "Request interrupted"), sys.stderr)
        return 130
    finally:
        if process.is_alive():
            process.terminate()
            process.join(2)
            if process.is_alive():
                process.kill()
                process.join()
        process.close()


if __name__ == "__main__":
    sys.exit(main())
