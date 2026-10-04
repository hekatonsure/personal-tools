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

## Retrieval and storage

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

`--jev` optionally ranks up to 24 redacted windows directly with TypeSafe. Credentials
come from `TYPESAFE_API_KEY` or its existing Windows user environment value. At most
six requests per retrieval, four-second HTTP timeouts, 24-hour exact-input cache,
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
```

Live probes consume the installed Codex account's usage. The cache comparison uses
matched prompts/seeds, but one sequential trial per policy is only a smoke comparison.
See [VALIDATION.md](VALIDATION.md) for observations and limits.
