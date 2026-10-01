# fm — Apple Foundation Models plugin

Use Apple's on-device model from Codex or Claude Code through the official
[Python SDK](https://apple.github.io/python-apple-fm-sdk/). The shared `ask` skill
supports text and image inputs, structured output, streaming, saved conversations,
independent batch requests, token inspection, and trusted Python tools.

Both clients load [`skills/ask/SKILL.md`](skills/ask/SKILL.md) and the same
[`scripts/fm_sdk.py`](scripts/fm_sdk.py) helper. Plugin managers may cache their
own copy; there are no separately maintained skill implementations.

## Requirements

- macOS 26+, an Apple Intelligence compatible Mac, and Apple Intelligence enabled.
- Xcode 26+ with the Xcode and Apple SDKs agreement accepted in Xcode.
- Python 3.10+ and `apple-fm-sdk` 0.2.1 installed for the Python interpreter used.
- Codex or Claude Code with plugin marketplace support.

In the commands below, `python3` must resolve to your chosen Python 3.10+
interpreter. Activate its virtual environment first if you use one, and install
the SDK with that same interpreter:

```sh
python3 -m pip install apple-fm-sdk==0.2.1
```

The skill reports missing dependencies and model availability errors. It does not
install packages or accept Apple terms automatically. Apple's native `fm` CLI is
an optional fallback, used only after making that choice explicit to the user.

Generation runs on-device. Your surrounding Codex or Claude Code session retains
its normal billing and data handling, including text passed to or returned from
the local model. Python tools you provide can access external services or files.

## Install from GitHub

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

Start a new session after installation. No custom shell installer is required.

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
constraints and SDK tools for access to task-specific information. See
[`examples/lookup.py`](examples/lookup.py) and Apple's
[SDK examples](https://github.com/apple/python-apple-fm-sdk/tree/main/examples).

## Apple terms and CLI fallback

SDK setup follows the applicable Apple/Xcode agreements and reported SDK errors.
The native CLI has its own first-use check: if it reports unaccepted terms, the
skill displays `fm license --show` and asks whether to Agree or Reject. Reject
stops that request; the plugin stays installed. To agree, the user runs
`sudo fm license` and answers Apple's prompt themselves. The assistant never
supplies acceptance. Apple's CLI acceptance applies to every user on that Mac.
This is first-use setup, not a plugin pre-install dialog.

## Development checks

```sh
python3 tests/test_sdk.py
python3 tests/test_sdk.py --live
claude plugin validate .
claude plugin validate skills
```

Checks require the SDK and an available on-device model; `--live` also generates
responses. Run native CLI and SDK checks together with `sh tests/smoke.sh`.
Set `PYTHON` to your SDK interpreter's absolute path when the shell's `python3`
resolves to another installation.

## License

The plugin source is [MIT licensed](LICENSE). Apple's SDK, models, CLI, and macOS
remain subject to their respective [Apple terms](https://www.apple.com/legal/sla/).
This is a community plugin, not an Apple product or endorsement.
