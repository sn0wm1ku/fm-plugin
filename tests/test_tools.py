"""Check custom tool registration offline; add --live for execution and resume."""

import argparse
import asyncio
import json
from pathlib import Path
import runpy
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from test_sdk import HELPER, ROOT, call, events, invoke


LOOKUP = ROOT / "examples" / "lookup.py"
BASE = f"import runpy\nLookupTool = runpy.run_path({str(LOOKUP)!r})['LookupTool']\n"
OUTPUT_ONLY = (
    'import apple_fm_sdk as fm\n@fm.generable("Extract a name")\n'
    'class Output:\n    name: str = fm.guide("The supplied name")\n'
)


def write_extension(directory, name, source):
    path = directory / name
    path.write_text(source, encoding="utf-8")
    return path


def offline(directory):
    import apple_fm_sdk as fm
    helper = runpy.run_path(str(HELPER))
    arguments = helper["parser"]().parse_args(["tools", "--extension", str(LOOKUP)])
    output = Mock()
    with patch.object(fm, "SystemLanguageModel", side_effect=AssertionError("Must not create a model")):
        with patch.dict(helper["dispatch"].__globals__, {"emit": output}):
            assert asyncio.run(helper["dispatch"](arguments, fm)) == 0
    output.assert_called_once()
    discovered = output.call_args.args[0]
    assert discovered["ok"] is True, discovered
    assert len(discovered["tools"]) == 1, discovered
    tool = discovered["tools"][0]
    assert tool["name"] == "lookup_catalog" and tool["description"], tool
    assert isinstance(tool["arguments_schema"], dict), tool
    assert "code" in json.dumps(tool["arguments_schema"]), tool
    assert call("tools", "--extension", LOOKUP) == discovered
    print("PASS: tool discovery returns schemas without creating or checking a model")

    invalid = {
        "no_exports.py": "value = 1\n",
        "non_callable.py": "create_tools = []\n",
        "non_list.py": "def create_tools():\n    return ()\n",
        "non_tool.py": "def create_tools():\n    return [object()]\n",
        "duplicate.py": BASE + "def create_tools():\n    return [LookupTool(), LookupTool()]\n",
        "blank_name.py": BASE + "class BadTool(LookupTool):\n    name = ' '\ndef create_tools():\n    return [BadTool()]\n",
        "blank_description.py": BASE + "class BadTool(LookupTool):\n    description = ' '\ndef create_tools():\n    return [BadTool()]\n",
        "empty_tools.py": "def create_tools():\n    return []\n",
        "output_only.py": OUTPUT_ONLY,
    }
    for name, source in invalid.items():
        extension = write_extension(directory, name, source)
        rejected = events(invoke("tools", "--extension", extension, success=False))
        assert len(rejected) == 1 and rejected[0]["ok"] is False, rejected
        assert rejected[0]["errors"][0]["type"] == "InvalidExtension", (name, rejected)
    output_arguments = helper["parser"]().parse_args([
        "respond", "Extract Alice", "--extension", str(directory / "output_only.py"),
    ])
    assert helper["configuration"](output_arguments, fm)["ok"] is True
    missing = events(invoke("tools", "--extension", directory / "missing.py", success=False))
    assert missing[0]["ok"] is False and missing[0]["errors"], missing
    print("PASS: malformed tools rejected; Output-only remains valid for respond")


def live(directory):
    extension = write_extension(directory, "two_tools.py", BASE + '''
class LocationTool(LookupTool):
    name = "lookup_location"
    description = "Look up a demo product's storage location by catalog code."

    async def call(self, args):
        code = args.value(str, for_property="code")
        return '{"code":"FM-DEMO-7","bin":"B42"}' if code == "FM-DEMO-7" else '{"error":"Unknown code"}'


def create_tools():
    return [LookupTool(), LocationTool()]
''')
    discovered = call("tools", "--extension", extension)["tools"]
    assert {tool["name"] for tool in discovered} == {"lookup_catalog", "lookup_location"}, discovered
    transcript = directory / "two-tools.json"
    call(
        "respond", "Use both lookup_catalog and lookup_location for code FM-DEMO-7. Report its name, stock, and storage bin.",
        "--extension", extension, "--save-transcript", transcript, "--greedy",
    )
    before = json.loads(transcript.read_text(encoding="utf-8"))["transcript"]["entries"]
    executed = [entry for entry in before if entry["role"] == "tool"]
    assert {entry.get("toolName") for entry in executed} == {"lookup_catalog", "lookup_location"}, executed
    assert "Amber notebook" in json.dumps(executed) and "B42" in json.dumps(executed), executed
    print("PASS: two tools from one extension executed and results recorded")
    call(
        "respond", "Check the location again now: call lookup_location for FM-DEMO-7. Do not reuse the previous answer.",
        "--extension", extension, "--resume", transcript, "--save-transcript", transcript, "--greedy",
    )
    after = json.loads(transcript.read_text(encoding="utf-8"))["transcript"]["entries"]
    new_calls = [entry for entry in after[len(before):] if entry["role"] == "tool"]
    assert any(entry.get("toolName") == "lookup_location" for entry in new_calls), new_calls
    assert "B42" in json.dumps(new_calls), new_calls
    print("PASS: resumed conversation executes a newly registered callback")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Also run tool inference on this Mac")
    arguments = parser.parse_args()
    with TemporaryDirectory(prefix="fm-tools-test-") as temporary:
        offline(Path(temporary))
        if arguments.live:
            live(Path(temporary))
