"""Run offline checks with Python, or add --live for real on-device inference."""

import argparse
import asyncio
import json
from pathlib import Path
import runpy
import struct
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
import zlib

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "fm_sdk.py"


def invoke(*arguments, stdin=None, success=True):
    result = subprocess.run(
        [sys.executable, str(HELPER), *map(str, arguments)],
        input=stdin, text=True, capture_output=True, timeout=90,
    )
    assert (result.returncode == 0) == success, (
        arguments, result.returncode, result.stdout, result.stderr
    )
    return result


def events(result):
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


def call(*arguments, stdin=None):
    result = events(invoke(*arguments, stdin=stdin))
    assert len(result) == 1 and result[0]["ok"] is True, result
    return result[0]


def offline(directory):
    assert "respond" in invoke("--help").stdout
    conflict = invoke("respond", "hello", "--greedy", "--top-k", "3", success=False)
    assert "not allowed with argument" in conflict.stderr, conflict.stderr
    for arguments in [
        ("respond", "hello", "--top-k", "0"),
        ("respond", "hello", "--top-p", "2"),
        ("respond", "hello", "--max-tokens", "0"),
        ("respond", "hello", "--timeout", "0"),
        ("respond", "hello", "--temperature", "-1"),
        ("respond", "hello", "--seed", "1"),
    ]:
        rejected = events(invoke(*arguments, success=False))
        assert rejected[-1]["errors"][0]["type"] == "InvalidInput", rejected
    malformed = directory / "malformed.json"
    malformed.write_text("{invalid", encoding="utf-8")
    destination = directory / "preserve.json"
    destination.write_text('{"preserve": true}', encoding="utf-8")
    original = destination.read_bytes()
    for flag in ("--schema", "--resume"):
        failed = events(invoke(
            "respond", "hello", flag, malformed,
            "--save-transcript", destination, success=False,
        ))
        assert failed[-1]["ok"] is False, failed
        assert failed[-1]["errors"][0]["type"] == "JSONDecodeError", failed
        assert destination.read_bytes() == original
    helper = runpy.run_path(str(HELPER))
    null_schema = directory / "null.json"
    null_schema.write_text("null", encoding="utf-8")
    rejected = events(invoke("respond", "hello", "--schema", null_schema, success=False))
    assert rejected[-1]["errors"][0]["type"] == "InvalidInput", rejected
    before = set(directory.iterdir())
    with patch.object(helper["os"], "replace", side_effect=OSError("simulated disk error")):
        try:
            helper["save_transcript"](destination, {"replacement": True})
        except OSError as error:
            assert str(error) == "simulated disk error", error
        else:
            raise AssertionError("Atomic replacement failure was swallowed")
    assert destination.read_bytes() == original
    assert set(directory.iterdir()) == before
    import apple_fm_sdk as fm
    unavailable = Mock()
    unavailable.is_available.return_value = (False, fm.SystemLanguageModelUnavailableReason(0))
    args = helper["parser"]().parse_args(["available"])
    with patch.object(fm, "SystemLanguageModel", return_value=unavailable):
        with patch.dict(helper["dispatch"].__globals__, {"emit": Mock()}) as namespace:
            assert asyncio.run(helper["dispatch"](args, fm)) == 1
            reported = namespace["emit"].call_args.args[0]
            assert reported["reason"] == fm.SystemLanguageModelUnavailableReason(0).name, reported
    print("PASS: offline validation, atomic write failure, and transcript preservation")


def png(path):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 32, 32, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress((b"\x00" + b"\xff\x00\x00" * 32) * 32))
        + chunk(b"IEND", b"")
    )


def live(directory):
    available = call("available")
    assert available.get("available") is True, available
    text = call("respond", "What is 2 plus 2? Reply with the digit only.", "--greedy")
    assert text["result"].strip() == "4", text
    print("PASS: availability and text generation")

    schema = ROOT / "examples" / "extract.json"
    extracted = call(
        "respond", "Extract the name and city: Alice lives in Sapporo.",
        "--schema", schema, "--greedy",
    )["result"]
    assert extracted == {"name": "Alice", "city": "Sapporo"}, extracted
    streamed = events(invoke("respond", "Count from one to five.", "--stream", "--greedy"))
    assert len(streamed) > 1 and streamed[-1]["ok"] is True, streamed
    assert isinstance(streamed[-1]["result"], str) and streamed[-1]["result"], streamed
    assert streamed[-2]["event"] == "snapshot", streamed
    assert streamed[-2]["text"] == streamed[-1]["result"], streamed
    print("PASS: constrained JSON and streaming")

    transcript = directory / "conversation.json"
    call("respond", "My favorite fruit is mango. Remember it for our conversation.", "--save-transcript", transcript, "--greedy")
    saved = json.loads(transcript.read_text(encoding="utf-8"))
    assert saved["transcript"]["entries"], saved
    resumed = call("respond", "What is my favorite fruit?", "--resume", transcript, "--greedy")
    assert "mango" in resumed["result"].lower(), resumed
    counts = call("tokens", "A short prompt", "--resume", transcript)
    assert counts["context_size"] > 0 and counts["prompt_tokens"] > 0, counts
    assert counts["transcript_tokens"] > 0, counts
    schema_counts = call("tokens", "A short prompt", "--schema", schema)
    assert schema_counts["schema_tokens"] is None, schema_counts
    print("PASS: transcript persistence, resume, and token accounting")

    original = transcript.read_bytes()
    for prompt, options, expected in [
        ("Explain how clouds form.", ["--timeout", "0.000001"], "TimeoutError"),
        ("Explain how clouds form.", ["--stream", "--timeout", "0.000001"], "TimeoutError"),
        ("Summarize these fruit inventory records:\n" + "\n".join(
            f"Record {index}: {index % 30 + 1} apples and {index % 17 + 1} pears."
            for index in range(counts["context_size"])
        ), [], "ExceededContextWindowSizeError"),
    ]:
        failed = events(invoke("respond", prompt, "--save-transcript", transcript, *options, success=False))
        assert failed[-1]["errors"][0]["type"] == expected, failed
        assert transcript.read_bytes() == original
    print("PASS: timeout and context-limit errors preserve the saved conversation")

    output_extension = directory / "output.py"
    output_extension.write_text(
        'import apple_fm_sdk as fm\n@fm.generable("A extracted name")\n'
        'class Output:\n    name: str = fm.guide("The name in the supplied text")\n',
        encoding="utf-8",
    )
    typed = call("respond", "Extract the name: Alice.", "--extension", output_extension,
                 "--top-k", "1", "--seed", "17", "--max-tokens", "100")
    assert typed["result"] == {"name": "Alice"}, typed
    typed_counts = call("tokens", "Alice", "--extension", output_extension)
    assert typed_counts["schema_tokens"] > 0, typed_counts
    print("PASS: typed generation with sampling options and schema token counts")

    batch = events(invoke("batch", ROOT / "examples" / "batch.jsonl", "--schema", schema, "--greedy"))
    assert [item["id"] for item in batch] == ["first", "second"], batch
    assert [item["result"]["name"] for item in batch] == ["Alice", "Bob"], batch
    mixed = events(invoke(
        "batch", "--continue-on-error", "--greedy", success=False,
        stdin='{"id":"before","prompt":"Reply OK"}\n{invalid\n{"id":"after","prompt":"Reply OK"}\n',
    ))
    assert len(mixed) == 3 and [item["ok"] for item in mixed] == [True, False, True], mixed
    assert mixed[-1]["id"] == "after", mixed
    print("PASS: independent batch records and continued processing after invalid JSON")

    tool_transcript = directory / "tools.json"
    call(
        "respond", "Use lookup_catalog to find FM-DEMO-7. Report its name and stock.",
        "--extension", ROOT / "examples" / "lookup.py",
        "--save-transcript", tool_transcript, "--greedy",
    )
    tool_entries = json.loads(tool_transcript.read_text(encoding="utf-8"))["transcript"]["entries"]
    executed = [entry for entry in tool_entries if entry["role"] == "tool"]
    assert any(entry.get("toolName") == "lookup_catalog" for entry in executed), tool_entries
    assert "Amber notebook" in json.dumps(executed), executed
    print("PASS: real Python tool callback confirmed by saved transcript")

    picture = directory / "red.png"
    png(picture)
    image_result = call("respond", "Describe this picture briefly.", "--image", picture, "--greedy")
    assert isinstance(image_result["result"], str) and image_result["result"], image_result
    print("PASS: image attachment inference")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Also run inference on this Mac")
    arguments = parser.parse_args()
    with TemporaryDirectory(prefix="fm-sdk-test-") as temporary:
        offline(Path(temporary))
        if arguments.live:
            live(Path(temporary))
