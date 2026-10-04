# memory-tool

Durable local memory for Codex. Public messages, tool calls and tool results stay
in SQLite. A master assistant delegates file work and recovers original evidence
through search and paged zoom. Context is reused until a reset is needed.

| Mode | What it controls |
|---|---|
| Native Codex MCP | Adds bounded retrieval/checkpoints to desktop and CLI chats; native context management remains in charge. |
| Codex gateway | Owns turn construction, context reuse and resets; disposable backend contexts share one durable logical chat. |

## Start

Install/authenticate Codex normally; no separate OpenAI API key is needed.
From this directory:

```powershell
uv sync
uv tool install .
memory-tool init --chat my-master --project C:/path/to/project
memory-tool chat --chat my-master --model gpt-5.6-luna
```

The explicit model above worked with the configured account. Choose an account-supported
model with `--model`; global settings are preserved. `/quit` exits, `/status` shows
metadata, and `/reset` rebuilds the working context on the next message.
The installed command is a self-contained snapshot, so switching repository branches
does not remove the running memory server. `uv run memory-tool` remains useful for development.

On Windows, do not force-reinstall a uv tool environment while its MCP process is
running: its Python executables are locked. For upgrades, build a wheel and install
it in a separate versioned runtime before changing the MCP command:

```powershell
uv build --wheel
$env:UV_TOOL_DIR = "$env:USERPROFILE/.local/share/memory-tool/runtimes/0.2.0/tools"
$env:UV_TOOL_BIN_DIR = "$env:USERPROFILE/.local/share/memory-tool/runtimes/0.2.0/bin"
uv tool install --from ./dist/memory_tool-0.2.0-py3-none-any.whl memory-tool
```

Use that version's absolute launcher in MCP/hook configuration. Preserve the old
runtime until its clients exit. The SQLite archive is outside these runtime folders.

Default Codex policy: reuse context, reset before a turn near **160k tokens**, and
request a **200k context window**, with native automatic compaction at the reset
threshold. Rebuilding injects at most **64k `o200k_base` tokens**, including metadata,
with up to **8k tokens** of recent source events. Change it with `--reset-at`,
`--context-limit`, `--budget`, `--recent`, or select `--mode fresh`.

Actual token/cache notifications and conservative local estimates drive rollover.
The 200k setting is a target, not a hard proof for every internal request: models,
tokenizers, hidden reasoning and long turns differ. Provider limits still apply.
Native compaction during a turn causes a rebuild on the next turn. Packet accounting
is exact for its named encoder, not every present or future model.
The MIT-licensed tokenizer data is bundled for offline startup; its source license
is in `src/memory_tool/assets/LICENSE.tiktoken`.

The master has only `memory_search`, `memory_zoom` and `delegate`. Its filesystem
environment, shell, inherited MCP servers and app tools are disabled. Workers receive
bounded tasks and return bounded evidence. They are read-only by default;
`--worker-write` permits project sandbox writes. Unexpected approval requests are
rejected. Four workers per turn maximum, one at a time; tasks are limited to 8k
tokens and turns to 240 seconds. This is a tool boundary, not protection against a
compromised local runtime.

## Native Codex

Bootstrap from the installed Connectome archive, then register a server:

```powershell
memory-tool import-connectome --chat my-master --project C:/path/to/project
codex mcp add memory_tool -- memory-tool serve --chat my-master --connectome C:/Users/you/.codex/connectome/history.sqlite
```

Pass the current absolute `project` path to each memory tool. The server routes to
that project's logical chat; `bff-master` remains the existing BFF scope. An omitted
project uses the configured default. Different projects cannot zoom each other's events.

Restart/reconnect MCP servers if a new registration is not visible in the existing
client. Tools: `memory_status`, `memory_search`, `memory_zoom`, `memory_checkpoint`.
On Windows, an absolute launcher path from `uv tool dir --bin` avoids stale app PATH state.
There is no active memo tool. With `--connectome`, retrieval refreshes from its local
public archive. Existing Connectome hooks/observer capture native chats; gateway
capture needs no Connectome. Native [MCP configuration](https://learn.chatgpt.com/docs/extend/mcp)
supports per-server and per-tool approval policies.

MCP retrieval does not replace or clear the native prompt. `memory_checkpoint`
saves a packet and returns metadata; it never reports that context was cleared.

`reset-codex --chat my-master --thread <id>` is an optional adapter requiring an idle,
fully captured thread and access to its owning server through `codex app-server proxy`.
It saves/verifies a checkpoint, waits for compaction completion, and injects memory;
the native summary remains. The Windows desktop control socket was unreachable on
the tested machine: **desktop remote reset is not verified**. Compaction/injection
passed on a disposable independently owned server. Gateway rollover and `/reset`
start a new backend context without deleting the archive or editing desktop rollouts.

### Automatic native recovery

The supported desktop route uses Codex lifecycle hooks, without the control socket:

```powershell
uv run python scripts/install_native_hooks.py --executable C:/Users/you/.local/bin/memory-tool.exe --connectome C:/Users/you/.codex/connectome/history.sqlite --reset-at 160000 --context-limit 200000
```

This adds two reviewable definitions to `~/.codex/hooks.json`, backs up changed
files, and preserves existing hooks and trust records. The optional context flags
set native targets in `config.toml`; omit them to retain current context settings.
Review and trust the two new memory-tool hooks through Codex `/hooks`. No trust
bypass is used. Restart/reload the client after changing its installed server.

`PreCompact` captures public transcript records and saves a checkpoint;
`SessionStart` after compact/resume/clear/startup restores a fresh project-scoped
packet. Retracted notes are re-evaluated. Capture verifies the transcript's session
and project, handles partial lines without advancing past them, and never ingests
hidden reasoning. Hooks run locally without models or network calls. Capture is
bounded to 64 MiB per invocation; incomplete capture is explicitly reported.
Existing Connectome import can bootstrap older history. Errors preserve the native
session and report that recovery failed rather than claiming success.

`memory_status.native_recovery` records **prepared** output, its checkpoint and
capture backlog; it does not prove model receipt. The packet including its envelope
is bounded by 64k o200k tokens and a conservative UTF-8/4 estimate for Codex's hook
output threshold. Native summaries and other installed hook output are additional
context, so this does not replace native history with an exact 64k allocation.

## Retrieval and storage

Approval-review transcript copies and replayed search/zoom results are excluded
from candidate ranking and packed history. They remain immutable and accessible by
event ID. Derived metadata is rebuilt on upgrade; no source records are deleted.
Overlapping windows are deduplicated before paid ranking, preserving separate
dated statements and changed decisions. Lexical search ignores common question
words and overfetches source windows before selecting distinct events.

Older history uses cached binary-tree literal excerpts, not generative summaries.
They may omit important facts. Search returns overlapping original windows; zoom
returns exact character pages and `next_offset`. Oversized recent events keep a
literal tail and a pointer. Replayed native events collapse semantically while every
source occurrence/raw public provenance remains stored. Generated memory packets
are excluded from repacking/search; superseded/retracted notes do not become current.

```powershell
uv run memory-tool search --chat my-master 'earlier decision'
uv run memory-tool zoom --chat my-master 42 --offset 0
uv run memory-tool pack --chat my-master --output C:/private/history.txt
```

`--jev` optionally ranks the first 12 distinct redacted source candidates directly
with TypeSafe, from a local shortlist of up to 24. Unranked candidates remain
available after ranked results. Credentials
come from `TYPESAFE_API_KEY` or its existing Windows user environment value. At most
three requests per retrieval, four-second HTTP timeouts, 24-hour exact-input cache,
no automatic retries, local fallback. Redaction is best-effort; enabling Jev exports
candidate conversation excerpts. Plain retrieval and packing stay local.

Store: `~/.local/share/memory-tool/memory.sqlite`, or `MEMORY_TOOL_HOME` / `--db`.
Use SQLite-aware backups: copying only the main file while WAL is active is insufficient.
Raw public records are immutable; hidden reasoning is excluded by gateway ingestion.
Never commit private archives, transcripts or keys.

This follows [OptMem's](https://github.com/VictorTaelin/OptMem) binary-tree/external-history
philosophy with Connectome's scoped notes and source evidence. It does not prove
perfect lifetime recall: FTS can miss paraphrases, previews lose detail, and packing
currently scans event metadata. Tested at 10k synthetic events, not hundreds of millions.
Claude work is deferred; its preliminary adapter is not a supported setup path here.

## Verify

```powershell
uv sync --group dev
uv run pytest -q
uv run ruff check src tests scripts --select F
uv run python scripts/live_codex.py
uv run python scripts/cache_compare.py
uv run python scripts/live_native_hooks.py
```

Live probes consume the installed Codex account's usage. The cache comparison uses
matched prompts/seeds, but one sequential trial per policy is only a smoke comparison.
The native hook probe requires the two memory-tool hooks to be trusted normally;
otherwise it exits with `needs_hook_trust` and makes no model calls.
See [VALIDATION.md](VALIDATION.md) for observations and limits.
