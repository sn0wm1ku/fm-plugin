# Custom tools for fm

Tools let the on-device model request a bounded lookup, calculation, or API call.
Use a programmatic host driver to lend existing host tools, script callbacks for
executable clients, or a Python extension for local implementations. Complete the [setup and terms
gate](../README.md#set-up-and-install) first; it also runs at runtime before
extensions are loaded or a bridge is created.

For weather, news, prices, or private records, the host assistant must first
decide which source data are missing. Discover the relevant tools and wire their
callbacks before asking fm to answer. For weather, use an available weather tool or
web search/fetch capability; preserve location, observation time, units, and
source URLs in the returned result. A plain offline response saying it cannot
access live data means the handoff was not completed.

## Callable MCP bridge

Prepare a self-contained task from the relevant conversation and authorized
sources. Include facts, constraints, and output requirements. Then bind the
selected **real host tool callbacks** to [`scripts/fm_host.js`](../scripts/fm_host.js).
The driver handles the complete callback loop in program code:

```text
Apple FM ↔ programmatic driver ↔ existing host tool callbacks
```

Use the reviewed installed driver. Load its source through an available host
file/exec tool **inside the program**, storing the returned text in a variable;
do not print the source into the LLM context on every run. It is an async function
expression, evaluated once in a programmable runtime that can call the actual
selected tools. Codex's `functions.exec` exposes callable tools
on `tools`; use their discovered names and schemas, including plugin namespaces.
The driver needs `start`, `resume`, and `cancel` bound to the installed
`fm_start`, `fm_continue`, and `fm_cancel` tools. Each selected tool also needs a
`call` function bound to its original host callable. Keep these references inside
the program runtime rather than sending function source to FM.

This example uses bindings discovered in the current host session:

```js
const runFM = eval("(" + reviewedDriverSource + ")");
const answer = await runFM({
  start: startFM,       // actual installed fm_start callable
  resume: continueFM,   // actual installed fm_continue callable
  cancel: cancelFM,     // actual installed fm_cancel callable
  prompt: "Use the current_utc_time tool and report its returned UTC time.",
  tools: [{
    name: "current_utc_time",
    description: "Return the current UTC time from the host clock.",
    inputSchema: {
      type: "object", properties: {}, required: [], additionalProperties: false
    },
    call: args => hostClock(args) // the original authorized tool, not an LLM call
  }],
  timeout_seconds: 120
});
text(answer); // emit the completed result, not each request or poll
```

`reviewedDriverSource` is the source text returned by the program's file/exec
tool call; `startFM`, `continueFM`, `cancelFM`, and `hostClock` reference actual
discovered host callables. This wiring reuses their implementations; it does
not require the user to write another clock, connector, or API client.

Evaluate only the reviewed installed driver source, never FM output. The driver
registers tool schemas, dispatches requests through the supplied callbacks, and
feeds their actual results back to FM. The host LLM runs the program once and
verifies the final answer. The returned `tool_calls` lists tool names and status;
full tool payloads stay inside the callback loop. Callback wrappers should preserve real errors and
source metadata, bound returned data, and enforce the authorized resource scope.
Use the [supported schema subset](#use-tools-already-available-to-the-host).
The driver's `timeout_seconds` is an integer from 5 to 300. Callbacks may accept
an `AbortSignal` as their second argument; cancellation cannot stop a host tool
that ignores it, so give external operations their own deadlines.

Codex's original tool permission checks still apply. Operations needing fresh
approval must stop for that approval. A standalone shell process instead needs
a programmatically reachable endpoint; use the Claude shim below for its native
tools. Report an observed connection limitation rather than silently reverting
to LLM-driven polling.

### Claude Code: lend its tools through a program shim

[`scripts/fm_claude.py`](../scripts/fm_claude.py) uses Claude Code's own
[`claude mcp serve`](https://code.claude.com/docs/en/mcp#use-claude-code-as-an-mcp-server)
to dispatch native tool callbacks directly, without a Claude model turn. Tools
that internally use models retain their own costs. For tools from
an already configured MCP server, supply its reviewed configuration with
`--config`. The shim loads the server's actual tool schemas, exposes only the
selected `--tools` to FM, and automatically executes and returns every callback.
The host LLM supplies context and scope once and receives the completed result.

The MCP client is responsible for confirmations: it does **not** inherit
Claude's interactive approval prompts. Every selected `server:tool` needs an
argument-scope JSON Schema in the required `--policy` file. Prepare that policy
from the user's authorization, using exact allowed resources and operations.
The shim validates FM's arguments against both the server schema and this policy
before dispatch. Selecting a tool is not approval for every action it can take.

Use `--prompt-file` for the prepared UTF-8 task and optional
`--instructions-file` for output constraints. `--timeout` is 5–300 seconds.
Without `--config`, the server name is `claude` and the shim starts
`claude mcp serve`. With `--config`, use the actual names from its `mcpServers`
map and the tools those servers advertise. Keep credentials in the reviewed
configuration/environment; never include them in the FM prompt or results.

For a read of one approved file, save this scope as `policy.json`, replacing the
path with the exact authorized file. The required `limit` bounds each read:

```json
{
  "claude:Read": {
    "type": "object",
    "properties": {
      "file_path": {"const": "/absolute/approved/file"},
      "limit": {"type": "integer", "minimum": 1, "maximum": 20},
      "offset": {"const": 1},
      "pages": {"enum": ["", "1"]}
    },
    "required": ["file_path", "limit"],
    "additionalProperties": false
  }
}
```

For this text-file example, ask FM to call `Read` with the fixed file path,
`limit: 8`, `offset: 1`, and `pages: "1"`, then report the first heading.
Write the task and expected output to `task.txt`, then run once with the approved
Python interpreter (shown as `python3`):

```sh
python3 scripts/fm_claude.py --tools claude:Read --policy policy.json --prompt-file task.txt --timeout 120
```

The callback calls Claude's actual `Read` tool. File content goes back to FM
inside the program loop; the host receives the final answer and compact tool
call metadata. Policies apply to arguments, so select a read path whose resolved
target is authorized and review any server-side behavior that changes scope.

### Script callbacks with `fm_run`

For an already authorized API/MCP client or local operation accessible from a
script, `fm_run` can own the callback loop in its server process. Supply
`prompt`, optional `instructions`, `timeout_seconds` (default 120), and `tools`
entries containing `{name, description, inputSchema, command}`. `command` is a
fixed argument array with an absolute executable path, for example
`["/approved/python3", "/absolute/reviewed_lookup.py"]`. Review it before use.
FM supplies JSON arguments on stdin; it never chooses executable code or argv.

A minimal read-only callback for one authorized catalog item:

```python
import json
import sys

def lookup(args):
    if not isinstance(args, dict) or args != {"code": "FM-DEMO-7"}:
        return {"ok": False, "errors": [
            {"type": "OutOfScope", "message": "Only FM-DEMO-7 is authorized."}
        ]}
    return {"ok": True, "result": {"code": "FM-DEMO-7", "stock": 37}}

try:
    arguments = json.load(sys.stdin)
except (ValueError, OSError) as error:
    response = {"ok": False, "errors": [
        {"type": "InvalidInput", "message": str(error)}
    ]}
else:
    response = lookup(arguments)
print(json.dumps(response))
```

Register that reviewed script using the actual approved paths:

```json
{
  "prompt": "Look up FM-DEMO-7 and report its stock.",
  "tools": [{
    "name": "lookup_catalog",
    "description": "Return stock for the authorized catalog item FM-DEMO-7.",
    "inputSchema": {
      "type": "object",
      "properties": {"code": {"type": "string", "enum": ["FM-DEMO-7"]}},
      "required": ["code"],
      "additionalProperties": false
    },
    "command": ["/approved/python3", "/absolute/reviewed_lookup.py"]
  }]
}
```

Stdout must contain exactly one JSON response: `{"ok":true,"result":...}` or
`{"ok":false,"errors":[{"type":"ToolFailed","message":"actual error"}]}`.
Keep it within 256 KiB; send diagnostics to stderr. The server handles callbacks,
deadlines, and process cleanup until the final result. The script enforces
resource authorization and gives external I/O its own deadline. It can call an
existing authorized MCP/API client directly; invoking a paid LLM to dispatch
the callback defeats this offloading path.

Use `tools: []` when the prompt already has all necessary information. Both
automatic routes remove per-callback host LLM turns; overall token or monetary
savings have not been measured. Inspect the final result and actual tool-use
status before relying on the answer; inspect a transcript separately when full
tool-result evidence is needed.

The server uses the setup receipt's Python interpreter and checks prerequisites
and agreement before generation. Missing dependencies require permission to
install; tool calls never accept Apple terms or install packages automatically.

## Try the existing Python tool

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
text to fm, or lend the existing connector through a programmatic host driver.
The driver calls the host's original tool with its existing authorization.
A standalone Python script instead requires a script-accessible authorized
client; copying a connector's name or schema does not create that connection.

## Use tools already available to the host

The following is the **manual compatibility relay**, for an explicitly requested
host-assisted workflow or debugging. Prefer the programmatic driver above for
offloading: the manual route involves the host LLM for each callback and polling.
Here, fm requests a selected host tool while the host assistant executes
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
