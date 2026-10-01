# Custom tools for fm

Tools let the on-device model request a bounded lookup, calculation, or API call
from Python. Start with the bundled [catalog lookup](../examples/lookup.py): it
uses fabricated data and reads no external service.

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

For the email translation workflow, the host assistant can retrieve the requested
emails through its authorized connector and pass their text to fm. If fm should
choose among already retrieved emails, a narrow local lookup tool can expose
those records by ID. Direct Gmail access from Python instead requires its own
authorized Gmail client. Host Codex/Claude Gmail and MCP connectors, including
their credentials, are not automatically available to an extension.

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
