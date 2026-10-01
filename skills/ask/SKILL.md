---
name: ask
description: Runs bounded tasks with Apple's on-device Foundation Model using the official Python SDK. Use when the user asks to use fm, Apple Foundation Models, or the local Apple model for summarization, rewriting, translation, extraction, classification, images, structured output, batch processing, custom Python tools, or a second opinion.
argument-hint: "<task or prompt>"
---

# Ask Apple Foundation Models

Use the shared Python helper to carry out the user's requested local-model task.
Resolve `../../scripts/fm_sdk.py` relative to **this installed SKILL.md's directory**
(`skills/ask/`), yielding `<plugin-root>/scripts/fm_sdk.py`. Use its absolute path
in commands. Do not hardcode the author's checkout, create another skill, or copy
the helper elsewhere. Both Codex and Claude Code use this same implementation.

1. Understand the outcome in the full conversation. Include relevant source
   material, confirmed facts, constraints, and the required output format. Keep
   unrelated chat history, credentials, and repositories out of the prompt.
2. Run `python3 <helper> available` with the chosen Python interpreter. Ensure
   `python3` resolves to the intended environment or use its absolute executable
   path; a host's default Python may be older or have different packages. It needs
   Python 3.10+, `apple-fm-sdk==0.2.1`, macOS 26+, Apple Intelligence enabled on a
   compatible Mac, and Xcode 26+ with its Apple SDK agreements accepted. Report
   actual dependency, availability, and agreement errors. Do not silently install
   packages, accept terms, change models, or route to a paid provider. The SDK's
   setup is separate from the optional native CLI license check below.
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
   environment. Codex/Claude connectors and their credentials are not inherited
   by the helper. For email translation, the host can retrieve the authorized
   emails and pass their text; direct email access needs a separately authorized
   API client. Never put secrets in instructions, schemas, or tool results.
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

## Optional native CLI fallback

If the SDK is blocked and the native CLI is suitable, explain the fallback before
using it; do not switch silently. Run `fm available`. If it specifically reports
unaccepted terms, run `fm license --show`, display the returned terms and linked
macOS license, and ask Agree or Reject. Reject stops this request and leaves the
plugin installed. Agree means the user runs `sudo fm license` in their terminal
and answers Apple's prompt. Never pipe an answer, accept on their behalf, or
change recorded acceptance. Explain that CLI acceptance applies to every user on
the Mac. Recheck availability after their action. This CLI-specific check must
not be imposed on SDK requests that are already available.

Use `fm respond --help` to check installed capabilities. For agent execution use
noninteractive `fm respond --model system --no-stream`, with stdin for arbitrary
text; reserve `fm chat` for a human-operated terminal.
