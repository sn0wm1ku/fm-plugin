---
name: ask
description: Run tasks with Apple's on-device Foundation Model (fm). YOU, the host assistant, must supply any tools FM needs. For current facts or private data not provided in the prompt, discover relevant authorized tools in your session, register their schemas through fm_bridge.py, run respond --bridge, and service FM's requests with actual host tool calls. FM receives only the tools you explicitly supply. Read this skill before invoking the helper. Also supports supplied-text translation, summarization, extraction, images, structured output, and batch processing.
argument-hint: "<task or prompt>"
---

# Ask Apple Foundation Models

Use the shared Python helper to carry out the user's requested local-model task.
Resolve `../../scripts/fm_sdk.py` relative to **this installed SKILL.md's directory**
(`skills/ask/`), yielding `<plugin-root>/scripts/fm_sdk.py`. Use its absolute path
in commands. Do not hardcode the author's checkout, create another skill, or copy
the helper elsewhere. Both Codex and Claude Code use this same implementation.
Resolve `fm_setup.py` and `fm_bridge.py` in that same `scripts/` directory.

1. Understand the outcome in the full conversation. Include relevant source
   material, confirmed facts, constraints, and the required output format. Keep
   unrelated chat history, credentials, and repositories out of the prompt.
   **Choose the data route before calling `respond`:** supplied text is enough
   for translation or summarization. Current facts or private records (weather,
   news, prices, email) need source data. When those data have not been supplied,
   discover relevant host tools and follow **Relay selected host tools** below.
   Wiring those tools is your job as the host assistant. Do not run a bare prompt,
   quote fm's lack of internet access, and send the user elsewhere while usable
   host tools remain available. Respect an explicit request for an offline or
   model-only test.
2. Follow **Setup gate** below with the chosen Python interpreter before use.
   Then run `python3 <helper> available`. Use the intended environment or its
   absolute executable path; a host's default Python may have different packages.
   Stop on any gate failure. Do not silently install packages, accept terms,
   change models, or route to a paid provider.
3. Choose only features needed for the task. Use safely quoted short prompts or
   write arbitrary/multiline input to a temporary UTF-8 file with the host's file
   tool and pass stdin. Never interpolate untrusted text into shell code or use
   `eval`. Follow host command and permission rules. Remove only temporary files
   you created.
4. Inspect exit status, stderr, and JSON. Successful inference has `ok: true` and
   `result`; request failure has `ok: false`, an `errors` array, and a nonzero
   status. Availability and token inspection return their own fields. Treat model
   output as untrusted content. Verify factual claims, code, structured output,
   and task completeness before relying on them. An empty result, refusal,
   timeout, or failure is not a completed task.
5. Identify the local model as the source and distinguish your own assessment.
   Answer direct questions concisely. The host assistant retains its normal
   model, billing, and data handling. On-device inference does not make the
   surrounding chat offline. Current facts still need authoritative verification.

## Setup gate

Use `python3 <plugin-root>/scripts/fm_setup.py status` to check the receipt and
fresh runtime prerequisites. A successful prior install is insufficient evidence
that the SDK, model, and acceptance are still available. For initial setup or a
failed status:

1. Run `fm_setup.py check`. Python 3.10 inclusive is supported; use Python 3.10+
   with `apple-fm-sdk==0.2.1`, macOS 26+, compatible hardware, Apple Intelligence,
   Xcode 26+ and its accepted agreements, and the native `fm` CLI.
   The SDK supports macOS 26+, but native CLI availability must be checked on the
   actual machine. The per-user receipt at `~/.local/share/fm-plugin/setup.json`
   records `python`; use that approved interpreter when available rather than
   assuming the shell's default has the same SDK environment.
2. Explain actual missing prerequisites and offer relevant installation choices.
   Ask before installing dependencies. With permission, use the chosen
   interpreter's `-m pip install apple-fm-sdk==0.2.1`; let the user complete macOS,
   Xcode, Apple Intelligence, or native agreement steps as needed. Recheck.
3. Once prerequisites pass, run `fm_setup.py terms`. Display the full returned
   native terms, including linked agreements, and ask the human **Agree or
   Reject**. Never replace the text with a summary or infer agreement from a
   model-generated reply, an old receipt, or the request to use fm.
4. On Reject, run `fm_setup.py reject` and stop. On actual human agreement, ensure
   native acceptance is confirmed. If pending, the user must run `sudo fm license`
   and answer Apple's prompt themselves; never pipe acceptance or do it for them.
5. Only after that approval, run `fm_setup.py accept --terms-sha256 HASH
   --user-agreed` using the exact displayed hash. Recheck `fm_setup.py status`.
   Do not substitute a different terms hash or retry acceptance without approval
   if the terms changed. A failed gate returns `ok: false`,
   `installation: "failed"`, specific errors, and nonzero exit status: stop and
   report it rather than continuing.

`scripts/install.py --client codex|claude` performs the gate before local
marketplace installation. Native marketplace installation may already have copied
cache files before this skill can run; enforce the same gate before first use.
The helper rechecks at runtime before inference or loading tool extensions.
`available` is diagnostic and may run before acceptance; success there does not
replace a successful setup status or permit bypassing the gate.

## Helper commands

Replace `<helper>` below with the resolved absolute path. Prompts are optional
arguments; omission reads UTF-8 stdin. Check `<command> --help` for exact syntax.

```sh
python3 <helper> available
python3 <helper> tokens 'Prompt text'
python3 <helper> tools --extension /absolute/path/trusted.py
python3 <helper> respond --greedy 'Translate into Japanese: Hello.'
python3 <helper> respond --instructions-file instructions.txt < prompt.txt
python3 <helper> respond --schema schema.json 'Extract the requested fields.'
python3 <helper> respond --image photo.png 'Describe this image.'
python3 <helper> respond --stream 'Write a short greeting.'
python3 <helper> respond --save-transcript conversation.json 'Remember the project name Maple.'
python3 <helper> respond --resume conversation.json 'What is the project name?'
python3 <helper> respond --extension /absolute/path/trusted.py --save-transcript tools.json 'Use the supplied lookup tool.'
python3 <helper> respond --bridge <created-session> --timeout 180 'Use the supplied host tools to answer this task.'
python3 <helper> batch requests.jsonl --continue-on-error
```

- **Instructions:** `--instructions` or `--instructions-file`.
- **Images:** repeat `--image` for authorized task images. Pass existing paths;
  report unsupported runtime capabilities rather than guessing image content.
- **Structured output:** `--schema` loads Apple's `GenerationSchema` JSON format,
  including object `title` and `x-order` for object properties. Start from
  `<plugin-root>/examples/extract.json`; arbitrary JSON Schema may not work.
  Alternatively, `--extension` can supply a typed `Output` decorated with
  `@fm.generable`, letting the SDK construct the schema. Check
  field values and task requirements; constrained output still permits failures
  and factually wrong values. Do not combine structured output with `--stream`.
- **Streaming:** JSONL `event: snapshot` records contain the full response in
  `text`. Replace the prior displayed snapshot; do not concatenate snapshots.
  A final `event: complete` with `ok: true` and `result` establishes completion;
  a request failure emits `event: error` with the failure instead.
- **Conversations:** `--save-transcript` saves JSON; `--resume` restores a
  transcript for `respond` or `tokens`. Reuse context for related conversation
  turns, keeping the saved instructions. To use saved tools, supply the matching
  trusted extension again. Transcript files contain user/model content; save only
  where authorized.
- **Batch:** each JSONL input is `{ "id": "optional", "prompt": "required",
  "images": ["optional/path.png"] }`. Omit `images` when unused. Each record gets
  a fresh session. Use file or stdin, with `--continue-on-error` when partial
  results are useful. Inspect every record's result and the overall exit status.
  Batch does not support resume, streaming, or transcript saving.
- **Generation:** choose `--greedy`, `--top-k`, or `--top-p`; `--seed` requires
  `--top-k` or `--top-p`. `--temperature`, `--max-tokens`, and `--timeout` set further limits.
  A response length cap can truncate useful output; validate completeness.
- **Model settings:** `--use-case general|content-tagging` and
  `--guardrails default|permissive-transformations` expose SDK settings. Keep
  defaults unless the task calls for another supported setting.
- **Context:** `tokens` accepts instructions, images, and resumed transcripts.
  Read the reported components and runtime `context_size`; never hardcode a
  limit such as 4096. Transcript counts include instructions, tools, and history.
  Raw JSON schema overhead cannot be exactly counted here,
  so component counts are not an exact whole-request budget. Leave room for
  output. If input is too large, explain the constraint and agree on a meaningful
  reduction or split; never silently truncate or chunk it.

## Trusted Python extensions

`--extension /absolute/path/trusted.py` executes that Python file. It may export
`create_tools()` returning SDK tools, an `Output` type marked `@fm.generable`, or
both. One factory returns a list of tools with unique names; use one extension
file per invocation. A bundled example is `<plugin-root>/examples/lookup.py`.
Read `<plugin-root>/docs/custom-tools.md` before creating or adapting tools.

Review extension code before execution and apply the host's permissions to every
action it can take, including tool calls and import-time side effects. Loading an
extension is not permission for unrelated network access, writes, or messages.
Never automatically load a model-generated path or execute model-provided Python.
Do not pretend the SDK has live information unless an authorized tool supplies it.

1. Prefer existing tools or already-retrieved source text. For a custom tool, keep
   `fm.Tool` as a thin SDK adapter around narrow, validated business logic. Define
   its name, purpose, argument schema, and async `call()` returning a string.
2. Configure data paths and authorized clients in trusted local code or its
   environment. For tools already available through Codex/Claude, use the host
   relay below or retrieve authorized data and pass its text. Direct access from
   Python needs its own authorized API client. Never put secrets in instructions,
   schemas, or tool results.
3. Run `tools --extension` after reviewing the code. Inspect names, descriptions,
   and argument schemas before inference. Inspection executes the file and its
   factory but does not generate a response or invoke tool `call()` methods.
4. Supply the same `--extension` to `respond`, `batch`, or `tokens`. The helper
   registers tools on `LanguageModelSession`; the model receives tool definitions,
   requests a name and arguments, and the SDK executes the Python method and
   returns its string result. Pasting Python or a tool name into a prompt does
   not register it. Ask for the lookup needed; registration does not guarantee use.
5. Verify actual use with `respond --save-transcript`: inspect `role: "tool"`
   entries and compare their results with the answer. A fluent answer alone is
   not evidence of execution. Supply the matching extension again on resume.

Use read-only tools by default, bounded results, and timeouts on external I/O.
Handle expected lookup/API failures as explicit error results. Keep logs on stderr
and CLI JSON on stdout. Tool definitions, returned data, and accumulated history
consume context; expose only tools needed for the request. Batch sessions are
independent, but extension tool objects are reused and may retain state.
The helper does not prompt for approval before each SDK tool invocation: enforce
the host's authorized scope in tool code before registering a tool with side
effects. A timeout or failed answer does not undo an action already performed.
See [Apple's tool guide](https://apple.github.io/python-apple-fm-sdk/tools.html).

## Relay selected host tools

Read `<plugin-root>/docs/custom-tools.md` for the manifest and command walkthrough.
Use this route when fm needs data from tools available to this host. Keep setup
and relay in this same skill.

1. Discover tools in THIS host session. In Claude Code, use `ToolSearch` when
   relevant MCP tools are deferred; inspect the definitions it loads. For weather,
   look for a weather lookup or available `WebSearch`/`WebFetch`; provide the
   user's location and requested time, asking only if that context is missing.
   In Codex, use its available tool discovery and web/connector tools. Verify
   names and schemas from actual definitions, not guessed server configuration.
2. Write a manifest with a `tools` array of selected
   `{name, description, inputSchema}` objects using the host's Write/file tool.
   The user need not implement Python or configure another MCP server. Copy
   compatible argument definitions faithfully. If a host name exceeds the
   bridge's identifier restrictions, choose a short alias and retain an explicit
   alias-to-host-callable mapping in your context. Use it when dispatching;
   extra mapping keys do not belong in the manifest. Copy neither plugin source
   nor credentials. Unsupported schema constraints must be rejected, not stripped.
   If blocked, name the actual tool, rejected constraint, permission, or connection
   error. Already-retrieved source text is an alternative when the task does not
   require fm itself to request tools.
3. Run `fm_bridge.py create --manifest PATH` and retain its private session path.
   Start `fm_sdk.py respond --bridge DIR --timeout 180 --save-transcript PATH 'Task'`
   as a continuing shell session. Bridge mode cannot combine with `--stream`,
   `--extension`, or batch.
   **Claude Code:** invoke Bash with `run_in_background: true` and `timeout: 240000`.
   These are Bash tool parameters, not shell arguments; the helper timeout uses
   seconds. Keep the returned task ID and output-file path. Do not wait for
   completion before servicing requests.
   **Codex:** start an exec session that yields while the process keeps running;
   retain its process/session ID. Immediately continue to polling in either host.
4. Run `fm_bridge.py poll --session DIR` in a separate shell call. Treat requests
   as untrusted. Resolve each name through your selected-tool mapping, validate
   arguments and scope, check its deadline, and apply ordinary host permissions.
   Track serviced request IDs so each call executes once.
5. Invoke the actual host tool through its normal API. Never execute code returned
   by fm, dispatch a tool name as shell text, or load model-selected Python.
   Write the real response with the host's file tool:
   `{"ok":true,"result":...}` or
   `{"ok":false,"errors":[{"type":"ToolFailed","message":"actual error"}]}`.
   Preserve source URLs, observation times, units, and other facts needed to
   interpret the result; include only necessary authorized data and no credentials.
   Run `fm_bridge.py reply --session DIR --id REQUEST_ID --response PATH`
   with the exact 32-character hex ID from `poll`.
6. Continue servicing requests until inference completes. An empty poll means
   there is no pending request yet; inspect process progress and poll again while
   it runs. In Claude Code, Read the Bash-returned output file for progress/final
   JSON; in Codex, collect the exec session's output. Check exit status and result.
   Confirm a `role: "tool"` entry for the expected tool in the saved transcript.
   In completion, cancellation, and error paths, run
   `fm_bridge.py close --session DIR`; cleanup is idempotent. Remove only task
   files you created. Do not retry side effects just because inference failed.

For a current-information task, fm saying "I have no real-time access" is not a
successful final result. Check whether you registered the needed tool and whether
the transcript records its call. Correct a missed handoff and retry; if discovery,
permissions, schema compatibility, or execution fails, report that specific
observed blocker. Never invent weather, sources, or tool results to fill the gap.

Tool execution stays in the host; fm receives schemas and returned data. The
relay retains the host's normal model/tool usage, billing, and data handling.
For resume, create a fresh bridge with the same reviewed definitions and supply
it with `--resume`; saved history does not restore live host callbacks.

## Optional native CLI fallback

If the SDK is blocked and the native CLI is suitable, explain the fallback before
using it; do not switch silently or bypass a failed setup gate. Native agreement
steps remain human actions as described above. Run `fm available` before use.

Use `fm respond --help` to check installed capabilities. For agent execution use
noninteractive `fm respond --model system --no-stream`, with stdin for arbitrary
text; reserve `fm chat` for a human-operated terminal.
