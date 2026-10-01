# fm — Apple Foundation Models plugin

Ask Apple's on-device language model from Codex or Claude Code using the native
`fm` CLI. Useful for bounded tasks such as rewriting, translation, summarization,
and extraction.

Both clients load the same [`skills/ask/SKILL.md`](skills/ask/SKILL.md) definition.
The two plugin manifests expose it to their respective clients. Plugin managers
may cache their own copy; there are no separately maintained implementations.

## Requirements

- A Mac with Apple's native `fm` CLI (tested on macOS 27.0.1).
- A working on-device model: `fm available` must succeed.
- Codex or Claude Code with plugin marketplace support.

The plugin requires no MCP server, daemon, API key, or additional packages.
It does not replace the host assistant's model. Your Codex/Claude Code session
retains its normal billing and data handling, including text passed to or returned
from the local model. Local generation does not make the surrounding chat offline.

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

## First-use license setup

When Apple's CLI reports unaccepted terms, the skill displays the current terms
from `fm license --show` and asks whether you want to Agree or Reject. Reject
stops model use for that request; the plugin stays installed.

To agree, review and answer Apple's own prompt in your terminal:

```sh
sudo fm license
```

The assistant never supplies the answer. Apple records acceptance for every user
on that Mac. Once `fm available` succeeds, the skill can run the requested task.
Existing Apple acceptance is reused. This is first-use setup, not a mandatory
pre-install dialog supplied by the plugin manager.

## Use

In Claude Code:

```text
/fm:ask Translate into Japanese: The test completed successfully.
```

In Codex, select the `fm` plugin's `ask` skill, or ask:

```text
Use the fm plugin to summarize this text with the local Apple model: ...
```

The skill uses noninteractive `fm respond --model system --no-stream`. It can
also use the CLI's token counting, structured-output schemas, and image inputs
when relevant. Output is attributed to the local model and checked before use.
For dates, prices, news, and other changing facts, verify against a current source;
the local model's answer alone does not establish that the information is current.

## Development checks

```sh
claude plugin validate .
claude plugin validate skills
sh tests/smoke.sh
```

The smoke test runs the real local model and checks a small deterministic task.
It requires Apple license setup and model availability to be complete.

## License

The plugin source is [MIT licensed](LICENSE). Apple's CLI, models, and macOS
remain subject to [Apple's separate license terms](https://www.apple.com/legal/sla/).
This is a community plugin, not an Apple product or endorsement.
