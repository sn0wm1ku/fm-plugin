# fm — Apple Foundation Models plugin

Use Apple's on-device model from Codex or Claude Code through the official
[Python SDK](https://apple.github.io/python-apple-fm-sdk/). The shared `ask` skill
supports text and image inputs, structured output, streaming, saved conversations,
independent batch requests, token inspection, trusted Python tools, and callable
MCP tools with a programmatic driver for lending existing host tools.

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

Install the plugin from the marketplace first. On first use, the shared `ask`
skill checks these requirements and offers to install missing dependencies with
your permission. You do not need to run Python setup commands yourself. Python
**3.10 is supported**, not just versions newer than it.

Generation runs on-device. Your surrounding Codex or Claude Code session retains
its normal billing and data handling, including text passed to or returned from
the local model. Python tools you provide can access external services or files.

## Set up and install

Add this repository in your client's marketplace UI, or use the commands below.
The marketplace installs the bundle; the `ask` skill handles runtime setup on
first use.

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

Both plugin manifests register a `SessionStart` prerequisite check using the same
shell helper. Codex resolves it through `PLUGIN_ROOT`; Claude Code uses
`CLAUDE_PLUGIN_ROOT`. In Codex, review and trust the plugin's hooks before they
run. The hook reports missing requirements to the assistant; the `ask` skill
handles installation permission and repair. It runs on session startup or resume,
after marketplace installation, and stays quiet when setup passes.

On first use, the assistant checks the runtime, explains any missing requirements,
and asks permission before installing Python or the required Python packages.
After permission, it runs the installation and checks again. macOS updates,
Xcode setup, and enabling Apple Intelligence may require your action.

The assistant displays Apple's full current terms and asks you to choose Agree
or Reject. If native acceptance is pending, you run `sudo fm license` yourself
and answer Apple's prompt. The assistant records your decision and verifies setup
before running a task. It reuses a valid receipt and rechecks prerequisites and
current terms on later uses.

For development workflows that check setup before copying the plugin,
`scripts/install.py --client codex|claude` remains available. The shared skill
runs the setup helpers for marketplace users.

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

The host LLM prepares a self-contained task and binds selected authorized tools
once. In Codex, [`scripts/fm_host.js`](scripts/fm_host.js) runs inside the host's
programmable tool runtime and calls its original tools. In Claude Code,
[`scripts/fm_claude.py`](scripts/fm_claude.py) connects directly to the native
`claude mcp serve` tools or selected existing MCP servers. Program code feeds
their results back to FM.
The host LLM receives the completed result for verification, without taking a
turn for each callback or progress poll.

```text
Apple FM ↔ programmatic driver ↔ existing host tool callbacks
```

Codex's driver uses `functions.exec` and its original tool permission checks.
Claude's MCP client must enforce the authorized scope itself; it does not inherit
interactive approval prompts. For script-accessible tools, `fm_run` also
supports fixed callback commands.

For ordinary text tasks and host tools, the plugin exposes these MCP tools in
both clients:

| Tool | Host assistant's responsibility |
| --- | --- |
| `fm_start`, `fm_continue`, `fm_cancel` | Bind these to the programmatic driver; it starts FM, services requests through the supplied host callbacks, and cleans up. Manual use remains available for explicit compatibility/debugging needs. |
| `fm_run` | Alternative for script callbacks: supply context and reviewed `{name, description, inputSchema, command}` tools. The call returns after automatic callbacks and inference finish. Use `tools: []` for a self-contained task. |

The drivers call the original tool implementations within the reviewed scope.
Script callbacks receive JSON arguments on stdin and return JSON on stdout.
Both automatic routes avoid host LLM turns for each tool call; total token or
cost savings have not been measured. See the
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

For existing host tools, use the [programmatic driver](docs/custom-tools.md#callable-mcp-bridge).
For script-accessible API/MCP clients, provide a reviewed callback script to
`fm_run`. The [manual host relay](docs/custom-tools.md#use-tools-already-available-to-the-host)
remains available for compatibility, but requires host LLM involvement for each
callback and is not the default offloading path.

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
python3 tests/test_setup_hook.py
python3 tests/test_install.py
python3 tests/test_bridge.py
python3 tests/test_bridge.py --live
python3 tests/test_mcp.py
python3 tests/test_mcp.py --live
node tests/test_host.mjs
python3 tests/test_claude.py
python3 tests/test_callbacks.py
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
