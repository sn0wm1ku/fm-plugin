# Custom tools for fm

Tools let the on-device model request a bounded lookup, calculation, or API call.
Use a Python extension for local implementations, or the host relay for selected
tools already available to Codex or Claude Code. Complete the [setup and terms
gate](../README.md#set-up-and-install) first; it also runs at runtime before
extensions are loaded or a bridge is created.

For weather, news, prices, or private records, the host assistant must first
decide which source data are missing. Discover the relevant tools and create the
relay before asking fm to answer. For weather, use an available weather tool or
web search/fetch capability; preserve location, observation time, units, and
source URLs in the returned result. A plain offline response saying it cannot
access live data means the handoff was not completed.

## Try the existing tool

Run these commands from the plugin directory. Elsewhere, use absolute paths for
the helper and extension, with the Python interpreter that has the SDK installed.

```sh
python3 scripts/fm_sdk.py tools --extension examples/lookup.py
python3 scripts/fm_sdk.py respond --extension examples/lookup.py --save-transcript lookup.json 'Use lookup_catalog to look up FM-DEMO-7 and report its name and stock.'
```

The first command lists the tool's name, description, and argument schema. It
loads executable Python and calls `create_tools()`, but performs no model
generation and does not invoke the tool's `call()` method. Review the extension
before inspection, including imports and factory code.

The example's catalog contains `FM-DEMO-7`, an Amber notebook with stock `37`.
Check that the response agrees with that source. Open `lookup.json` and inspect
entries whose `role` is `tool`: the recorded tool result establishes execution.
A plausible answer by itself does not. Keep the transcript only if needed; it
contains the prompt, tool results, and model output.

## How a tool reaches the model

1. `--extension` names a trusted Python file on your machine.
2. The helper executes the file and calls its `create_tools()` factory.
3. Returned tool objects are passed to `fm.LanguageModelSession(tools=...)`.
4. The model receives the tool names, descriptions, and argument schemas along
   with the task. It may choose a tool and generate arguments for it.
5. The SDK invokes that tool's async `call()` method with `fm.GeneratedContent`.
6. The returned string goes back into the model's context, informing its answer
   or further tool calls.

Python implementation code stays in the local process. Supply it through
`--extension`, rather than pasting it into the prompt. Describing a function in a
prompt does not register executable behavior. The model chooses whether to call
a registered tool; registration and instructions do not guarantee execution.

These are the SDK's native tool callbacks, documented in Apple's
[tool guide](https://apple.github.io/python-apple-fm-sdk/tools.html) and
[Tool API](https://apple.github.io/python-apple-fm-sdk/api/tools.html).

## Define your extension

Adapt [examples/lookup.py](../examples/lookup.py) as the smallest complete example.
Its parts map directly to the SDK:

| Part | Purpose |
| --- | --- |
| `@fm.generable` argument type | Defines typed inputs and field guidance. |
| `name` | Unique, descriptive tool identifier, such as `lookup_catalog`. |
| `description` | Explains what the tool returns and when to use it. |
| `arguments_schema` | Returns the argument type's `generation_schema()`. |
| `async call(self, args)` | Extracts fields, runs the operation, returns a string. |
| `create_tools()` | Takes no arguments; returns a list of instantiated `fm.Tool` objects. |

Use `args.value(str, for_property="code")` to extract the example's code. Return
plain text or `json.dumps(...)`; the SDK callback requires a string. Keep the
`fm.Tool` subclass as a thin SDK adapter, with validation and business logic in
ordinary functions where useful.

A single extension can provide several tools: return them from one factory,
for example `return [CatalogTool(), StockTool()]`. Each name must be unique.
Names and descriptions must be nonempty. The factory is synchronous; tool
`call()` methods are asynchronous.
The helper accepts one extension path per invocation. An extension must export
`create_tools()`, a typed `Output` decorated with `@fm.generable`, or both.
`Output` constrains the final answer; it is separate from tool argument schemas.
Use either `Output` or `--schema`, and omit structured output when streaming.

## Provide data, configuration, and credentials

Resolve a bundled data file relative to `Path(__file__).parent`, or obtain an
explicit path from environment configuration. Avoid depending on the shell's
working directory. Initialize authorized API clients in trusted local code or
the factory. Required configuration should fail clearly when missing.
Keep definitions in the extension or install their local Python package in the
same interpreter environment. Loading the extension does not automatically add
its directory to Python's import path for sibling modules.

Credentials stay inside the Python process or its authorized credential store.
Never include them in a prompt, schema, description, returned data, or logs.
Keep tool results limited to information necessary for the user's request.
The local model's inference runs on-device; a Python API client can still send
data over the network according to its implementation.

For email translation, the host can retrieve authorized emails and pass their
text to fm, or relay selected connector calls as described below. Direct Gmail
access from Python requires its own authorized client. The host relay keeps
Codex/Claude Gmail and MCP credentials with their existing host tools.

## Use tools already available to the host

The bridge lets fm request a selected host tool while the host assistant executes
it using its existing authorized connector or tool API. This is a live relay:
the host must service requests while model generation waits. Python does not
receive the host's credentials or installed plugin implementation.

Create a manifest with only the reviewed tool definitions needed for this task.
In Claude Code, discover deferred MCP tools through the session's `ToolSearch`
tool and inspect the loaded definitions; built-in `WebSearch` and `WebFetch` may
also provide current sources when available. The host writes the manifest itself.
For a long or incompatible host tool name, use a short valid alias and retain its
mapping to the actual callable in the host's context. Keep that mapping outside
the manifest. The bridge shares selected definitions and results; it does not
require copying a plugin or reconnecting its account.
This illustrative definition assumes the host has a matching authorized email
read operation; substitute its actual reviewed definition and argument names:

```json
{
  "tools": [{
    "name": "read_email",
    "description": "Read one email authorized for this translation task by its ID.",
    "inputSchema": {
      "type": "object",
      "properties": {"id": {"type": "string", "description": "Authorized email ID"}},
      "required": ["id"],
      "additionalProperties": false
    }
  }]
}
```

The manifest accepts `object`, `array`, `string`, `number`, `integer`, and
`boolean` types, with `title` and `description`. Objects use `properties`,
`required`, and `additionalProperties: false`; arrays use `items`; strings may
have `enum`, a non-negative `minLength`, and `format: "uri"`. The latter two are
explained to the model and validated before a request reaches the host, including
inside arrays and nested objects. Other formats remain unsupported. This accepts
the string constraints used by Claude Code's WebSearch and WebFetch tools without
discarding them. Optional object properties are supported. Tool names start with a
letter and contain up to 64 letters, digits, or underscores. Property names also
start with a letter and use letters, digits, or underscores; Python keywords
cannot be property names. The top-level input schema must be an object.
Unsupported schema
keywords are rejected. Do not remove constraints to make an incompatible host
schema pass; choose a compatible tool or pass already retrieved source text.

Write the manifest with the host's file tool, then create a private relay session:

```sh
python3 scripts/fm_bridge.py create --manifest selected-tools.json
```

The response is `{"ok":true,"session":"/absolute/private/session/path"}`.
Substitute that path for `<session>` below. Start inference in a continuing shell
execution session, so the host remains able to run polling and connector calls:

```sh
python3 scripts/fm_sdk.py respond --bridge <session> --timeout 180 --save-transcript translated.json 'Read the authorized email IDs supplied with this task and translate them to Cantonese.'
python3 scripts/fm_bridge.py poll --session <session>
```

In Claude Code, launch the `respond` command with these **Bash tool parameters**
(replace paths with the approved interpreter and installed helper):

```json
{
  "command": "\"<python>\" \"<helper>\" respond --bridge \"<session>\" --timeout 180 --save-transcript \"<transcript>\" \"Use the supplied tools to answer the current-weather request.\"",
  "run_in_background": true,
  "timeout": 240000
}
```

Keep Bash's returned task ID and output-file path, and immediately poll in a
separate Bash call. Waiting for the model's final answer first would leave its
tool request unserviced. Invoke each requested host tool using Claude's normal
tool-call interface, then Write the response file and run `reply` below. Use
`Read` on the background output file to inspect progress and final JSON. For
Codex, use a yielding exec session and retrieve output with its session ID.
See Claude's [background command documentation](https://code.claude.com/docs/en/tools-reference#background-commands).

`poll` returns `requests`, each containing `id`, `name`, `arguments`, and
`deadline`. For each pending request, the host checks that the name matches a
selected tool and its arguments remain within the user's authorization. The
host then calls the real tool through its normal API and permission flow.
Check the request deadline and remember serviced IDs to avoid duplicate calls.
An empty poll is not completion; check the running process and continue polling.
An fm tool name is data, never a shell command; model-provided code is never run.

Write the actual safe result to a response file, using one of these shapes:

```json
{"ok": true, "result": {"id": "actual-id", "body": "Actual authorized email text"}}
```

```json
{"ok": false, "errors": [{"type": "LookupFailed", "message": "Actual error"}]}
```

`result` can be a JSON value. Keep the whole response under 256 KiB and return
only relevant authorized data, without credentials. Preserve failures honestly.
Use the request's exact `id` (a 32-character hex identifier) to reply:

```sh
python3 scripts/fm_bridge.py reply --session <session> --id <request-id> --response response.json
```

Continue polling and replying until inference finishes, then check its final
JSON and exit status. Tool results feed back into fm's answer and can be checked
in the saved transcript. Close in every completion, cancellation, or error path:

```sh
python3 scripts/fm_bridge.py close --session <session>
```

Closing is idempotent, so host cleanup can run even if inference already closed
the session. Remove only temporary task files you created. After cancellation,
do not execute late requests or retry writes without verifying what happened.

If fm still says it lacks live access, inspect whether the intended tool was
registered and actually called. Correct a missed handoff instead of presenting
that response as completion. If tool discovery, permissions, schema conversion,
or execution blocks the task, report that observed error and any source-data
alternative appropriate to the user's request.

Bridge mode supports `respond` with transcripts, but rejects streaming, batch,
and simultaneous Python extensions. On resume, create a new bridge with the same
reviewed definitions and pass `--bridge` alongside `--resume`. The shared `ask`
skill manages setup and relay; the surrounding host model/tool activity retains
its normal costs and data handling even though fm inference is on-device.

## Keep execution bounded

- Prefer a specific read-only action over arbitrary shell, SQL, paths, or URLs.
  Validate argument values and resource scope inside `call()`, even with typed
  arguments. Tool descriptions guide generation; code enforces authorization.
- Use limits on result counts and string sizes. Return the fields needed for the
  task rather than entire API responses, documents, or credentials.
- Give external I/O its own timeout and bound retries. Keep blocking I/O off the
  async event loop where necessary. The helper's request timeout does not roll
  back a remote action or reliably cancel SDK callback threads. Give each tool
  its own I/O deadlines instead of relying on generation cancellation.
- Return expected failures as explicit strings, for example serialized JSON with
  `ok: false` and an error message for an unknown ID or unavailable service. Do
  not turn failure into a successful-looking empty answer or invented data.
- Write diagnostic logs to stderr; stdout is reserved for the helper's JSON.
- Review import-time actions as well as `call()`. Loading an extension executes
  arbitrary trusted Python and is subject to the host's normal permissions.
- For writes, messages, or transactions, enforce the user's authorized scope
  before exposing the action. The helper does not provide a per-call approval
  dialog. Design writes to handle retries safely and verify actual completion;
  a failed generation does not prove that an action was never performed.

## Continue, batch, and inspect context

```sh
python3 scripts/fm_sdk.py respond --extension examples/lookup.py --resume lookup.json 'What stock did the tool return?'
python3 scripts/fm_sdk.py tokens --extension examples/lookup.py 'Look up FM-DEMO-7.'
python3 scripts/fm_sdk.py batch requests.jsonl --extension examples/lookup.py
```

Supply the matching trusted extension again when restoring a transcript: saved
history does not restore executable Python callbacks. Use `--save-transcript`
again if the continued conversation should be saved.

Each batch record gets a new model session, but the extension factory runs once
per helper invocation and its tool objects are reused. Keep tools stateless for
independent records, or explicitly scope any cached data to the authorized task.

Tool schemas, results, and conversation history consume context. Register only
relevant tools and return concise results. `tokens` reports the runtime context
size and component counts, including tool definitions in the session transcript;
leave room for future tool results and the answer. Counts are not a guarantee of
the complete future request budget.
