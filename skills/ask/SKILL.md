---
name: ask
description: Run tasks with Apple's on-device Foundation Model (fm). Prepare context and authorized tool scope once. Lend Codex tools through scripts/fm_host.js using actual callbacks, or Claude tools through scripts/fm_claude.py and its MCP server. Program code feeds tool results to FM without host LLM callback turns. Verify the final answer.
argument-hint: "<task or prompt>"
---

# Ask Apple Foundation Models

Resolve the plugin root as `../..` relative to this installed `SKILL.md`'s directory.
Both clients use this same skill. Use helpers under that root; never hardcode
the author's checkout or create a second skill implementation.

Prepare a self-contained task from the relevant conversation: authorized source
material, facts, constraints, and output requirements. Pass source material in
`prompt` and constraints in `instructions`. Keep unrelated history and secrets
out. Supplied text is sufficient for translation or summarization; current facts
and private records need actual source data or authorized tools.

The default flow is **Apple FM ↔ program shim ↔ existing host tools**.
The host LLM prepares context and scope once, runs the shim, and verifies its
completed result. Program code handles every tool callback and progress poll.
Do not ask the user to reimplement an existing host tool, or route callbacks
through another paid LLM. Overall token/cost savings have not been measured.

## Runtime and setup gate

Marketplace installation is the user entry point. Run setup helpers yourself;
do not ask users to run Python setup or plugin installer commands.

Use the approved Python recorded in `~/.local/share/fm-plugin/setup.json` when
available; the shell's default Python may have different packages. Verify that
the interpreter exists and supports Python 3.10+. If it is missing or too old,
discover an existing compatible interpreter first. If none is available, verify
an installation method for this machine, explain the exact installation scope,
ask permission, and install only after approval. Recheck the interpreter.
Run `<python> <plugin-root>/scripts/fm_setup.py status` with that interpreter.
Runtime prerequisites and the current terms agreement must pass before
inference, extensions, or tool callbacks. A failed gate pauses inference while
you offer setup or repair; it does not end the setup flow automatically.

For first setup or a failed status, follow
`<plugin-root>/README.md#set-up-and-install`:

- Run `fm_setup.py check` and inspect each failed check's `remediation`.
  Python 3.10+, `apple-fm-sdk==0.2.1`, `mcp==2.2.0`, compatible macOS/hardware,
  Apple Intelligence, Xcode agreements, and the native FM CLI must be available.
- For failed SDK or MCP checks, show the exact interpreter, pinned packages,
  and returned installation commands and ask permission. After approval, run
  those `remediation.argv` commands yourself with safe argument handling. Only
  repair failed dependencies; do not install packages when their checks pass.
  If installation fails, report the actual error and offer a verified remedy;
  do not bypass environment protections or silently switch interpreters.
- Rerun `check` after installation. For platform settings or agreements that
  require human action, explain the returned remediation and wait for it to be
  completed. On rejection, stop. Continue to terms only when prerequisites pass.
- Run `fm_setup.py terms`. Display its full actual terms and ask the human
  **Agree or Reject**. Never infer consent from this invocation or a model reply.
- If native acceptance is pending, the human runs `sudo fm license` and answers
  Apple's prompt. Never pipe acceptance or do it for them.
- On actual agreement, use `fm_setup.py accept --terms-sha256 HASH --user-agreed`
  with the displayed hash, then recheck status. Restart the client's FM MCP
  connection after dependency repair so it uses the approved environment.
  Resume the original task only after status passes. On rejection, run
  `fm_setup.py reject` and stop. Changed terms require a new human decision.

A valid receipt can be reused while status verifies its current hash and
prerequisites. `available` is diagnostic and never substitutes for the gate.
`scripts/install.py --client codex|claude` gates before local installation;
marketplace clients may copy files earlier, so the same gate applies at use.
Never silently install packages, accept terms, change models, or use a paid
provider to get past a failed setup.

## Codex: lend actual host callbacks

1. Discover the selected authorized host tools and installed `fm_start`,
   `fm_continue`, and `fm_cancel`. Use their real schemas and namespaces.
2. Inside `functions.exec`, load the reviewed installed
   `<plugin-root>/scripts/fm_host.js` through an available host file/exec tool.
   Keep the source in a program variable; do not print it into LLM context on
   every run. Evaluate only this trusted async function expression, never
   model-generated code. The runtime exposes callable tools through `tools`.
3. Call the driver with `start`, `resume`, and `cancel` bound to those actual
   FM callables; `prompt`, `instructions`, `timeout_seconds`; and selected
   `tools: [{name, description, inputSchema, call}]`. Each `call` references
   the original host tool. Restrict argument/resource scope in its wrapper.
   Only schemas go to FM; executable references remain in the program runtime.
4. Await the complete driver call. Emit only its final result and compact
   `tool_calls` metadata. The program services all intermediate callbacks.
   Original host permissions still apply; stop for any required fresh approval.

The timeout is 5–300 seconds. A callback may accept an `AbortSignal` as its
second argument. Tools ignoring cancellation need their own I/O deadlines.
See `<plugin-root>/docs/custom-tools.md#callable-mcp-bridge` for the binding
example. A tool name without a real callable is not an executable connection.

## Claude Code: lend its native and configured MCP tools

Run `<plugin-root>/scripts/fm_claude.py` with the approved Python:

- `--tools server:ActualToolName [...]` selects the tools to lend.
- `--prompt-file PATH` supplies the prepared UTF-8 task.
- `--instructions-file PATH` optionally supplies constraints.
- `--timeout SECONDS` is 5–300, default 120.
- `--policy PATH` is required: a JSON map from each selected `server:tool`
  to an argument JSON Schema restricting authorized resources and operations.
- Omit `--config` to use Claude's own `claude mcp serve`, named `claude`.
  Supply `--config PATH` for selected existing `mcpServers` configurations.

The shim reads real server tool schemas and handles callbacks directly, without
a Claude model turn for callback dispatch. Selected tools may internally use
models and retain their own costs. Prepare scope once from the user's authorization. Its MCP
client must enforce that scope because it does not inherit interactive approval
prompts. The guide contains a `claude:Read` example restricted to one exact file
and bounded lines. Keep credentials in reviewed configuration/environment.

Run the shim once and inspect its final JSON. Do not manually service its tool
requests. Report actual configuration, schema, permission, or connection errors;
do not silently revert to an LLM callback loop.

## Other supported routes

- `fm_run` accepts `prompt`, optional `instructions`, `timeout_seconds`,
  and `tools: [{name, description, inputSchema, command}]` for script-accessible
  operations. The fixed argv begins with an absolute executable. Reviewed code
  receives JSON arguments on stdin and writes `{"ok":true,"result":...}` or
  `{"ok":false,"errors":[{"type":"...","message":"..."}]}` on stdout, within 256 KiB.
  Diagnostics go to stderr. Code enforces resource scope before execution.
  The server automatically runs callbacks and returns results to FM.
- Use `tools: []` when the supplied task contains all necessary information.
- Images, structured output, streaming, transcripts/resume, batch, token counts,
  and trusted Python extensions use `scripts/fm_sdk.py`. Read only the relevant
  sections of `README.md#direct-sdk-helper` and `docs/custom-tools.md`.
  Review executable extensions before loading. Never run FM-generated code.
- Manual `fm_start`/`fm_continue` and file relay instructions in the guide are
  compatibility/debugging routes requiring explicit need, not the default.

## Verify the result

Inspect status and errors. A refusal, empty result, timeout, or failed lookup is
not successful completion. Verify facts, output format, and task completeness;
tool-call metadata establishes execution status, not answer correctness. Preserve
source URLs, timestamps, and units where needed. Identify FM as the answer's
source and distinguish the host's assessment. Never invent tool results.

Avoid untrusted shell interpolation. Use the host's file tool for arbitrary
multiline prompts and remove only temporary files you created. A cancelled or
failed inference does not undo an action already performed. On-device inference
does not change the surrounding host's normal billing or data handling.
