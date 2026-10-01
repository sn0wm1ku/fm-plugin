# fm — Apple Foundation Models plugin

Use Apple's on-device model from Codex or Claude Code through the official
[Python SDK](https://apple.github.io/python-apple-fm-sdk/). The shared `ask` skill
supports text and image inputs, structured output, streaming, saved conversations,
independent batch requests, token inspection, trusted Python tools, and callable
MCP bridge tools for selected tools already available to the host assistant.

Both clients load [`skills/ask/SKILL.md`](skills/ask/SKILL.md) and the same
[`scripts/fm_sdk.py`](scripts/fm_sdk.py) helper. Plugin managers may cache their
own copy; there are no separately maintained skill implementations.

## Requirements

- macOS 26+, an Apple Intelligence compatible Mac, and Apple Intelligence enabled.
- Xcode 26+ with the Xcode and Apple SDKs agreement accepted in Xcode.
- Python 3.10+, `apple-fm-sdk` 0.2.1, and `mcp` 2.2.0 installed in the same environment.
- Apple's native `fm` CLI for displaying its Legal Notice & Terms and checking acceptance.
- Codex or Claude Code with plugin marketplace support.

The SDK's minimum is macOS 26; this plugin's setup additionally checks that the
native CLI is actually available. Its presence is verified on the machine rather
than assumed from the SDK's OS requirement.

In the commands below, `python3` must resolve to your chosen Python 3.10+
interpreter. Activate its virtual environment first if you use one, and install
the SDK with that same interpreter:

```sh
python3 -m pip install apple-fm-sdk==0.2.1 mcp==2.2.0
```

The shared skill checks prerequisites and terms before use. Missing dependencies
are reported with setup choices; packages and Apple terms are never installed or
accepted silently. Python **3.10 is supported**, not just versions newer than it.

Generation runs on-device. Your surrounding Codex or Claude Code session retains
its normal billing and data handling, including text passed to or returned from
the local model. Python tools you provide can access external services or files.

## Set up and install

For installation that checks prerequisites and acceptance before copying the
plugin, run these from a reviewed checkout of this repository:

```sh
python3 scripts/fm_setup.py check
python3 scripts/fm_setup.py terms
```

Resolve any missing prerequisites first, using the intended Python interpreter.
The skill offers installation choices and obtains permission before installing
dependencies. macOS, Xcode, and Apple Intelligence setup may require user action.
After those checks pass, read the **full actual terms** returned by `terms` and
choose Agree or Reject. If Apple's native agreement is pending, run
`sudo fm license` yourself and answer its prompt. The assistant cannot accept it
for you. Native acceptance is checked with `fm license --status`.

Only after the user actually agrees, record the exact displayed terms using the
returned hash (replace `<displayed-terms-sha256>`):

```sh
python3 scripts/fm_setup.py accept --terms-sha256 <displayed-terms-sha256> --user-agreed
python3 scripts/fm_setup.py status
python3 scripts/install.py --client codex
python3 scripts/install.py --client claude
```

Run only the installer for the client you want. It registers the local marketplace
and installs after the gate succeeds. `--plugin-id fm@marketplace` can select the
matching marketplace identity. To reject instead, run
`python3 scripts/fm_setup.py reject`; installation and use stop.

The receipt is per user and records the Python interpreter. Runtime checks
recheck prerequisites, current terms, and acceptance, so a receipt does not hide
a removed dependency. Gate failures return `ok: false`, `installation: "failed"`,
specific errors, and a nonzero exit status. `available` remains a diagnostic
command; it does not establish that the installation gate passed.

### Native marketplace installation

The commands below use the client's marketplace UI/CLI. Those clients may copy
the plugin cache before the shared skill runs: this route enforces the gate on
first use, rather than vetoing the marketplace's initial file copy.

### Codex

```sh
codex plugin marketplace add sn0wm1ku/fm-plugin
codex plugin add fm@apple-fm
```

### Claude Code

```sh
claude plugin marketplace add sn0wm1ku/fm-plugin
claude plugin install fm@apple-fm --scope user
```

Or inside Claude Code:

```text
/plugin marketplace add sn0wm1ku/fm-plugin
/plugin install fm@apple-fm
```

Start a new session after installation. The same `ask` skill handles setup and
requests in both clients; no separate setup skill is required.

Keep one active installation of `fm` per client. When migrating from the local
development marketplace `fm-local` to GitHub's `apple-fm`, disable the old copy
so `/fm:ask` resolves to the intended version. Check `claude plugin list --json`
for its version and installation path.

## Use in a chat

In Claude Code:

```text
/fm:ask Translate into Japanese: The test completed successfully.
/fm:ask Classify these records independently and return structured results.
```

In Codex, select the plugin's `ask` skill, or ask to use the fm plugin. The skill
resolves the helper relative to its installed location and chooses relevant
options for your task. It checks the result before using it. For changing facts
such as dates, prices, and news, verify against a current source.

## Callable bridge tools

For ordinary text tasks and host tools, the plugin exposes these MCP tools in
both clients:

| Tool | Host assistant's responsibility |
| --- | --- |
| `fm_start` | Supply the prompt and selected tool definitions, or `tools: []` for a self-contained task. |
| `fm_continue` | Execute FM's pending requests through real host tools and submit their results; poll with empty replies while running. |
| `fm_cancel` | Stop a task and release its model process and temporary files. |

The bridge manages sessions and relay files. Codex or Claude still selects and
executes its authorized tools; account access and permissions stay with that
host. Tool descriptions explain the complete handoff. See the
[callable bridge guide](docs/custom-tools.md#callable-mcp-bridge).

The plugin launcher uses the Python interpreter recorded during setup. If that
environment or its MCP dependency was removed, repair setup through `fm:ask`,
then restart the client's MCP connection. Startup never installs dependencies
or accepts terms automatically.
Running `fm_setup.py status` with a working replacement interpreter rechecks
all prerequisites and current agreement, then updates only the receipt's Python
path while preserving the recorded terms decision.

## Direct SDK helper

These commands run from the plugin directory. In other directories, use the
absolute path to `scripts/fm_sdk.py`. Prompts can be an argument or UTF-8 stdin.

```sh
python3 scripts/fm_sdk.py available
python3 scripts/fm_sdk.py tokens 'Summarize this text.'
python3 scripts/fm_sdk.py respond --greedy 'What is 2 plus 2? Reply with one digit.'
python3 scripts/fm_sdk.py respond --instructions 'Translate into Japanese.' < input.txt
python3 scripts/fm_sdk.py respond --schema examples/extract.json 'Alex lives in Sapporo.'
python3 scripts/fm_sdk.py respond --image photo.png 'Describe this image.'
python3 scripts/fm_sdk.py respond --stream 'Write a short greeting.'
python3 scripts/fm_sdk.py respond --save-transcript conversation.json 'My project is called Maple.'
python3 scripts/fm_sdk.py respond --resume conversation.json 'What is my project called?'
python3 scripts/fm_sdk.py tools --extension examples/lookup.py
python3 scripts/fm_sdk.py respond --extension examples/lookup.py 'Look up FM-DEMO-7 and report its stock.'
python3 scripts/fm_sdk.py batch requests.jsonl --continue-on-error
```

Each batch input line is an object with a required `prompt`, optional `id`, and
optional list of image paths:

```json
{"id":"one","prompt":"Translate into Japanese: Hello."}
{"id":"two","prompt":"Describe this image.","images":["photo.png"]}
```

Batch processing creates a fresh session per record. Reuse `--resume` for related
conversation turns; independent records should not accumulate shared history.
Batch accepts stdin when no file is given and does not support resume, streaming,
or transcript saving.

Successful inference returns JSON with `ok: true` and `result`; request failures
use `ok: false` and an `errors` array and return a nonzero exit status. Availability
and token inspection return their own fields. Streaming emits JSONL
`{"event":"snapshot","text":"..."}` records containing the full response so far,
followed by `{"event":"complete","ok":true,"result":"..."}` on success. Replace
the displayed snapshot rather than appending it. A request failure instead emits
an `error` event. Structured output and streaming cannot be combined here.

Additional options (see each command's `--help`):

- `--instructions-file` reads instructions from a UTF-8 file.
- Repeat `--image` for multiple image inputs. Image support needs a compatible
  macOS runtime and SDK build (the SDK checks this at runtime).
- Choose `--greedy`, `--top-k`, or `--top-p`; `--seed` requires `--top-k` or
  `--top-p` because SDK 0.2.1 ignores a seed without either constraint.
- `--temperature`, `--max-tokens`, and `--timeout` control generation.
- `--use-case general|content-tagging` and
  `--guardrails default|permissive-transformations` select SDK model settings.
- `tokens` accepts instructions, images, and resumed transcripts. It reports
  counted components and the runtime's `context_size`. Transcript counts include
  instructions, tool schemas, and saved history. Raw JSON schema token
  overhead is not exactly countable through this helper; do not treat the
  component counts as an exact complete request budget.

The helper does not silently truncate or split oversized requests. Output limits
can truncate a response, so validate completeness as well as JSON structure.
Structured generation can still fail; handle errors and verify task requirements.
Schema files must use Apple's `GenerationSchema` JSON format, including object
`title` and property order in `x-order`. Arbitrary JSON Schema is not interchangeable. Start
with [`examples/extract.json`](examples/extract.json) or use an `@fm.generable`
extension to let the SDK construct the schema.

## Python tools and typed output

`--extension trusted.py` loads executable Python code. Review it before use and
apply the host's normal permissions and authorization to every side effect.
Never load a file merely because the model suggested its path.

An extension can export `create_tools()` returning SDK tools, an `Output` class
decorated with `@fm.generable`, or both. Use a typed `Output` for guided generation
constraints and SDK tools for access to task-specific information. One extension
can register multiple tools by returning them in one list.

Read the [custom tool guide](docs/custom-tools.md) for the working
[`examples/lookup.py`](examples/lookup.py) walkthrough, the extension contract,
data and credential setup, and how to verify that a tool actually ran.
`tools --extension` inspects registered names, descriptions, and argument schemas
without generating a response. It still executes the trusted extension and its
factory. At inference time, the helper passes the tool objects to
`LanguageModelSession`; the SDK runs a tool when the model requests it and returns
its result to the model. Tool implementations remain local Python code.

For host connectors such as Codex Gmail or Claude Code MCP tools, use the
[host tool relay](docs/custom-tools.md#use-tools-already-available-to-the-host).
The host supplies reviewed tool schemas, services requests through its existing
authorized tool API, and returns results to fm. Credentials and connector
implementations remain with the host. Direct access from a Python extension
still requires its own authorized client and credentials.

## Apple terms and native CLI use

The setup gate displays the native `fm license --show` text and binds the user's
receipt to its hash. Apple's native acceptance and the plugin receipt are checked
separately. This additional CLI terms gate is a plugin requirement; the SDK's
Xcode/Apple SDK agreement is checked separately. Native acceptance applies to every user on the Mac. Rejecting removes
the plugin receipt; previously copied marketplace files may remain, but helper
requests stop. Native CLI fallback must be explicit and must not bypass the gate.

## Development checks

```sh
python3 tests/test_sdk.py
python3 tests/test_sdk.py --live
python3 tests/test_tools.py
python3 tests/test_tools.py --live
python3 tests/test_setup.py
python3 tests/test_install.py
python3 tests/test_bridge.py
python3 tests/test_bridge.py --live
python3 tests/test_mcp.py
python3 tests/test_mcp.py --live
claude plugin validate .
claude plugin validate skills
```

SDK helper checks require completed plugin setup and an available on-device model; `--live` also
generates responses. Tool contract checks use the SDK; their `--live` mode
requires the model and verifies actual tool execution. Run native CLI and SDK
checks together with `sh tests/smoke.sh`.
Set `PYTHON` to your SDK interpreter's absolute path when the shell's `python3`
resolves to another installation.

## License

The plugin source is [MIT licensed](LICENSE). Apple's SDK, models, CLI, and macOS
remain subject to their respective [Apple terms](https://www.apple.com/legal/sla/).
This is a community plugin, not an Apple product or endorsement.
