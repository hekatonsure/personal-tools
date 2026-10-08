# memory-tool

Durable local memory for Codex and Claude Code. Public messages, tool calls and tool results stay
in SQLite. A master assistant delegates file work and recovers original evidence
through search and paged zoom. Context is reused until a reset is needed.

| Mode | What it controls |
|---|---|
| Native Codex MCP | Adds bounded retrieval/checkpoints to desktop and CLI chats; native context management remains in charge. |
| Native Claude hooks + MCP | Captures Claude transcripts and restores bounded memory after compaction; implemented with offline validation, live receipt pending. |
| Codex gateway | Owns turn construction, context reuse and resets; disposable backend contexts share one durable logical chat. |
| Codex HTTP proxy | Archives and reduces outgoing history; optional native thread routing lets one local service serve multiple projects. |

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
$env:UV_TOOL_DIR = "$env:USERPROFILE/.local/share/memory-tool/runtimes/0.4.0/tools"
$env:UV_TOOL_BIN_DIR = "$env:USERPROFILE/.local/share/memory-tool/runtimes/0.4.0/bin"
uv tool install --from ./dist/memory_tool-0.4.0-py3-none-any.whl memory-tool
```

Use that version's absolute launcher in MCP/hook configuration. Preserve the old
runtime until its clients exit. The SQLite archive is outside these runtime folders.

For an already approved installation, `scripts/bridge_runtime.py` can instead
redirect new invocations of the old launcher to the tested new runtime. It checks
the new executable's SHA256, preserves the old Python entry module beside it, and
changes only that entry module. It never replaces a running executable or modifies
hook definitions, approval records or Codex configuration. Review both paths and
pass `--old-module`, `--new-executable` and `--expected-sha256`. Retargeting an
existing bridge additionally requires its exact `--expected-old-module-sha256`.
Already running MCP processes keep their loaded code until reconnect; newly
launched lifecycle hooks use the upgrade immediately. Keep both runtimes on disk.

Default Codex policy: reuse context, reset before a turn near **160k tokens**, and
request a **200k context window**, with native automatic compaction at the reset
threshold. Rebuilding defaults to a **24k `o200k_base` token ceiling**, including
metadata, with up to **8k tokens** of recent source events. **64k** remains the
maximum optional packet budget. Change it with `--reset-at`,
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
client. Tools: `memory_status`, `memory_search`, `memory_zoom`, `memory_feedback`,
`memory_note`, `memory_checkpoint`.
On Windows, an absolute launcher path from `uv tool dir --bin` avoids stale app PATH state.
`memory_note` saves source-linked project findings, decisions, preferences and
commitments; status notes require an expiry. With `--connectome`, retrieval refreshes from its local
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

On Linux/macOS, without Connectome:

```bash
uv tool install .
memory-tool init --chat home --project "$HOME"
codex mcp add memory_tool -- "$(uv tool dir --bin)/memory-tool" serve --chat home
uv run python scripts/install_native_hooks.py --executable "$(uv tool dir --bin)/memory-tool"
```

This adds three reviewable definitions to `~/.codex/hooks.json`, backs up changed
files, and preserves existing hooks and trust records. The optional context flags
set native targets in `config.toml`; omit them to retain current context settings.
Review and trust the new memory-tool hooks through Codex `/hooks`. No trust
bypass is used. Restart/reload the client after changing its installed server.
Connectome's own SessionStart hook injects its memory as well; with both installed,
restored context contains both.

`Stop` captures each finished turn, so search sees the current session.
`PreCompact` captures public transcript records and saves a checkpoint;
`SessionStart` after compact/resume/clear/startup restores a fresh packet scoped to
the actual native session within its project. Retracted notes are re-evaluated.
Capture verifies the transcript's session
and project, handles partial lines without advancing past them, and never ingests
hidden reasoning. Hooks run locally without models or network calls. Capture is
bounded to 64 MiB per invocation; incomplete capture is explicitly reported.
Existing Connectome import can bootstrap older history. Errors preserve the native
session and report that recovery failed rather than claiming success.

Empty conversations receive a bounded startup orientation (selection policy 6).
Git identifies the starting directory's repository, including nested repositories
and linked worktrees. Up to three recent sessions from that repo appear first,
with dated task/outcome excerpts; up to five recent topics from other locations
follow with literal reply excerpts too. Identical full opening requests within
one project share a slot, with a related-session count. Final-answer metadata is
preferred over progress updates; otherwise a recent informative reply is used.
Short final corrections remain eligible even after a longer success report.
When a final reply hands off documentation, a preceding substantive reply can
accompany it with its own date and source ID; the latest reply remains visible.
A fuller related conversation may supply an additional dated reply, without
claiming it overrides the newest session. Explicit disposable smoke tests are
fallback entries. These are presentation heuristics, not proof of completion.
The orientation uses at most 4k tokens within the existing packet budget,
including at most 2.4k for general references. Noise/stale/
wrong feedback, expired status and known low-value records remain excluded.
Each reference carries its original project and event IDs for search/zoom.
Archive bindings are not migrated: sessions started from a home directory remain
there, discoverable through the general index. Outside a Git repo, startup says
so and supplies only general references. Existing conversations retain their own
session history rather than receiving a fresh unrelated-session overview.

`memory_status.native_recovery` records **prepared** output, its checkpoint and
capture backlog; it does not prove model receipt. It describes the project's
latest recovery operation, which may belong to another chat: check its thread and
`selection.session` before attributing it to the current conversation.
The packet including its envelope is bounded by the requested budget (24k by
default) in o200k tokens and a conservative UTF-8/4 estimate for Codex's hook
output threshold. Native summaries and other installed hook output are additional
context, so this does not replace native history with an exact total allocation.

### Native Claude Code (live validation pending)

Claude uses the same archive and memory tools, with a separate transcript parser.
`Stop` captures public messages and tool blocks; `PreCompact` checkpoints them;
`SessionStart` restores a bounded packet after compact/resume/clear/startup.
This does not replace Claude's own compaction or native summary. It makes no
Claude model calls. The optional background Jev labeler behaves as on Codex;
`MEMORY_TOOL_AUTO_LABEL=0` disables it.

From this directory, after installing the updated memory-tool runtime:

```bash
uv tool install --force .
memory-tool init --chat home --project "$HOME"
uv run --frozen python scripts/install_claude_hooks.py \
  --executable "$(uv tool dir --bin)/memory-tool" --chat home --dry-run
# Omit --dry-run to install the reviewed definitions.
```

On Windows, use the separate-runtime upgrade procedure above instead of replacing
a running tool. The installer uses Claude's documented
[hook settings](https://code.claude.com/docs/en/hooks) in `~/.claude/settings.json`
and [user MCP configuration](https://code.claude.com/docs/en/mcp) in `~/.claude.json`.
It backs up changed files, preserves unrelated settings and permissions, and
refuses conflicting memory-tool definitions. `--path`, `--mcp-path` and `--db`
allow explicit destinations; a custom database is passed to both hooks and MCP.
No Claude process is launched by the installer. Reload Claude to use the setup.

Existing Supermemory hooks and tools are preserved. If both systems inject startup
context, both remain active until you choose which injection to keep.

Claude's recorded working directory can change during a conversation. Capture
anchors the archive project to the first public record's directory and keeps that
scope in its cursor. Use the project path printed in the restored packet when
calling memory tools. Session filtering separates Claude and Codex conversations
within a project; project-wide search can find either.

Capture preserves source UUIDs and timestamps, excludes thinking blocks, and
retains generated compaction summaries only as excluded evidence. Partial lines
are retried. Rewritten transcripts are rescanned when the cursor's trailing bytes
change or the file shrinks; UUIDs prevent duplicate imports. Capture has a 64 MiB
per-hook allowance and an 8 MiB per-line limit. Remaining bytes are reported as
backlog; oversized lines require separate handling. Mixed-session/fork transcripts
and sidechains are rejected rather than imported into the wrong conversation.
Native compaction, forks and actual model receipt still need live Claude checks.

### HTTP proxy and tool-result compaction (experimental)

The first proxy stage forwards Anthropic Messages or OpenAI Responses HTTP
requests to one explicitly configured upstream. It streams response entity bytes
unchanged, including thinking signatures and encrypted reasoning. Requests pass
unchanged by default; `--compact-at` enables the rule described below. See the protocol references for
[Anthropic streams](https://platform.claude.com/docs/en/build-with-claude/streaming)
and [Responses streams](https://developers.openai.com/api/reference/resources/responses/streaming-events).

By default, run one proxy instance per conversation, using an existing archive chat
and a distinct session ID. For example, these commands start listeners only; they do not
launch a model or change any client configuration:

```bash
uv run --frozen memory-tool proxy --chat home --session my-claude-conversation \
  --provider anthropic --upstream https://api.anthropic.com --port 8787
# Alternatively, for a Responses API client:
uv run --frozen memory-tool proxy --chat home --session my-responses-conversation \
  --provider openai --upstream https://api.openai.com --port 8788
```

For a persistent native Codex service, add `--codex-home /absolute/path/to/.codex`.
The proxy resolves consistent UUIDs from Codex protocol metadata against its newest
local `state_*.sqlite` thread registry, with native hook records as an early-start
fallback. Each native thread gets independent compactor state and its project's
archive chat, matching MCP project routing. The cache holds at most 128 threads;
eviction/restart rebuilds from persisted evidence. A thread's project binding is
persistent. Missing/conflicting identity, unknown projects, changed project bindings
or unreadable registries pass through without capture or rewriting; prompt text is
never used to guess a project. Native `/responses/compact` requests pass through
verbatim without capture. `GET /health` exposes bounded aggregate routing counts.

The 2026-10-07 installation on this machine uses this mode with a user service,
the existing ChatGPT login and unchanged main model/reasoning settings. Details,
restart instructions, backups and disable command are in
[the live setup note](md_archive/codex-live-setup.md).

The upstream URL's base path is prepended to the incoming path. With the examples
above, clients POST to `/v1/messages` or `/v1/responses` on the local listener.
An upstream ending in `/v1` instead takes incoming `/responses`. Anthropic
`/v1/messages/count_tokens` also passes through, without conversation capture.
Authentication headers come from the client and pass to that upstream. The proxy
does not read credential files, follow redirects, retry requests or configure
subscription routing. Codex ChatGPT login has now passed an isolated live probe
using a separately configured custom Responses provider and the ChatGPT upstream;
see the probe command below. Claude subscription routing remains unverified.

Public text and supported tool blocks are added to the archive. Each exchange
also records request/response byte counts, SHA256 hashes and capture status in
the `operations` table (`proxy:` IDs). Raw HTTP bodies, headers, thinking and
encrypted reasoning are not persisted. Images/documents get fingerprints, and
unknown block types are omitted. Replayed full-history prefixes reuse source IDs;
incremental Responses inputs remain distinct. Fixed proxy session IDs participate
in packet selection but do not automatically map to native hook session IDs.
Native Codex routing instead uses the actual thread identity.

Archive or parsing failures leave forwarding intact and are reported separately.
Complete public responses are captured only after the response finishes. A
broken stream closes the client connection without inventing a terminal event.
`delivered` means the proxy finished writing, not proof that the client consumed
the response. Response capture is bounded to 8 MiB; compressed bodies pass
unchanged but are currently marked `unsupported_encoding` instead of parsed.
Unknown formats, incomplete streams and oversized captures retain status metadata.

The listener binds only `127.0.0.1`, accepts local Host headers, rejects browser
Origin headers, and permits plain HTTP upstreams only at literal loopback IPs.
Request bodies require one Content-Length and are limited to 32 MiB; chunked
uploads, WebSockets and other routes are unsupported. HTTP hop-by-hop headers and
connection framing are rebuilt. See VALIDATION.md for the live Codex results and
the remaining provider/transport boundaries.

#### Optional threshold-based compaction

Add `--compact-at 128000` to enable local rule-based compaction. `--keep-turns 3`
and `--result-chars 500` are the defaults. Above the threshold, the proxy keeps the
head and tail of eligible older text tool results, adding a `memory_zoom` event ID
for their archived originals. The original must be saved successfully before any
reduction. Configure the client's memory MCP tools to use the same archive and
project/chat so those IDs can be retrieved.

Only tool-result string values change. All surrounding request bytes—including
system instructions, tool schemas, user messages, assistant text, call arguments,
thinking signatures, encrypted reasoning and image blocks—stay intact. The latest
three actual user turns stay whole; Anthropic tool-result-only user messages do
not count as new turns. The rule retains tool calls/results in place with their
IDs and error flags, and does not summarize or change call arguments.

The reduced prefix is reused while its original history matches, until the
reduced request crosses the threshold again. A changed prefix invalidates reuse.
This state lives only in the proxy process; restarting recomputes it from the
original request. The optional summary-tree mode below replaces this rule.
No summary model is called by the tool-result rule.

The threshold is an estimate: the larger of `o200k_base` tokens and UTF-8 bytes/4
for the JSON body. It is not the provider's exact context count or a hard limit.
Large recent turns or preserved text may leave the result over budget; diagnostics
then report `target_met: false`. Compaction never removes protected content to
force the target. Token-count endpoints remain unchanged, so this does not
guarantee suppression of a client's native compaction.

Compressed or HTTP-signed requests, incremental/server-managed Responses history,
duplicate JSON keys, parser failures and archive failures pass through unchanged.
Missing/ambiguous archive IDs or unmatched tool results are left intact. Compaction
is opt-in. Live Codex compaction and source retrieval have passed; live encrypted
reasoning/signature behavior and general answer quality remain unverified.

Exchange diagnostics in `operations.detail` include `compaction` (reason, local
before/after estimates, cutoff and target status), `request_sha256` for the original,
and `forwarded_request_sha256`/`forwarded_request_bytes` for the actual upstream
body. This records the transformation without persisting private reasoning.

#### Incremental summary tree (experimental)

OpenAI reasoning items can leave the forwarded window with complete older steps.
They are never decrypted, archived, or sent to the summary model. Recent reasoning
and its tool calls stay intact. A live medium-reasoning Codex run passed five
distinct compactions, a proxy-process restart, and recovery of two details absent
from forwarded history and summaries. All 29 requests succeeded. Summary latency
remains substantial; see VALIDATION.md for measurements and limits.

Add `--summary-tree` alongside `--compact-at` to replace a complete older history
span with a persistent view of summary lines. The first user message, top-level
instructions/tool schemas, and the latest `--keep-turns` user turns remain exact.
The default is free literal lines. Model-written lines require an explicit model:

```bash
uv run --frozen memory-tool proxy --chat home --session my-conversation \
  --provider openai --upstream https://api.openai.com --port 8788 \
  --compact-at 128000 --summary-tree --summary-model gpt-6.1-sol
```

This starts a listener; it does not configure or launch the main client. When a
request needs reduction, `--summary-model` permits summary calls through the local
Codex installation and its authentication. The adapter uses
[`codex exec --ephemeral`](https://learn.chatgpt.com/docs/non-interactive-mode),
low effort, an isolated temporary directory, and disabled hooks, MCP, tools and
plugins. It removes inherited provider base-URL overrides to avoid proxy recursion.
The adapter and proxy have passed an isolated live Codex ChatGPT-login probe,
including model-generated nodes and native MCP traversal back to an original
source. This is a functionality check, not a general summary-quality evaluation.

Each leaf covers one archived public event. Messages already fitting 512 UTF-8
bytes use their own flattened text. Longer messages can get a model summary;
merges use the two child lines without surrounding context. The view appends new
leaves and, beyond `--summary-lines 32`, merges the adjacent pair most overdue
relative to its size/age. Merged nodes never split during forward progress.
Parent/child links preserve every source even when its detail disappears from a
line. `memory_zoom event="tree:<id>"` opens children and eventually exact event
IDs; ordinary search still searches original evidence, not generated summaries.
Use the updated memory MCP process with the same archive/project as the proxy.

SQLite stores nodes and the current view separately from immutable raw events.
Replays and restarts reuse saved nodes; changed/reordered/rewound history resets
the view. Cache identities include the summary prompt, model and byte limit.
Fallback lines are cached too, so a later successful model call does not silently
rewrite an earlier prefix. The proxy reuses its injected prefix until the local
threshold is crossed again. Appending lines preserves earlier lines; a fold can
change an older portion, so cache hits are not guaranteed.

`--summary-calls 8` and `--summary-seconds 30` bound model attempts and their total
time allowance per tree update. No retries run automatically. Missing models,
timeouts, empty/multiline/oversized answers and exhausted allowances use literal
head/tail excerpts. Inputs over 64,000 UTF-8 bytes also use literal excerpts.
These bounds limit work, not monetary cost; model summaries consume the selected
Codex authentication route's usage. `--result-chars` applies only to the older
tool-result mode.

The proxy replaces older spans only at user-turn boundaries, with complete,
uniquely paired tool calls/results and exact archived public evidence. Known,
completed OpenAI reasoning items inside those spans are removed with their model
steps; even their readable reasoning summaries are excluded from the memory tree.
A reasoning-only interrupted step, unknown reasoning fields, Claude thinking or
signatures, images, citations, and unknown content/metadata remain barriers.
The preserved prefix and recent suffix, including reasoning, stay byte-identical.
Cached replacements recheck the user boundary and tool pairs against each request.
Signed/encoded/provider-managed requests and failures pass through.
Local token estimates may remain above the threshold (`target_met: false`).
Native hook packets still use the existing literal-excerpt selector. The live
Codex probe is reproducible with synthetic data and temporary per-thread settings:

```bash
uv run --frozen python scripts/live_proxy_codex.py --mode tree \
  --summary-model gpt-6.1-sol --native-mcp
```

This uses the existing Codex account's quota. Reports go outside the repository
under `~/.local/share/memory-tool/evals/codex-proxy/`; normal client configuration
is unchanged. The live test covered public assistant phases, including tree-node
zoom through the actual MCP server. It did not exercise encrypted reasoning,
Claude, long-session endurance or general repeated-merge quality.

The more realistic probe uses native coding tools and a
mid-session requirement change, then gates restart/recall on proven omission:

```bash
uv run --frozen python scripts/live_codex_session.py --effort medium
```

Its reports live under `~/.local/share/memory-tool/evals/codex-session/`. It stops
before restart/recall if no details were compacted away, and exits nonzero on
failure. It does not modify normal client configuration.

### Agent feedback

`memory_feedback` lets an agent judge a record it was shown: `noise`, `stale` or
`wrong` (with a note) omit that document and its replayed copies from restored older
history; `useful` keeps it in the continuity reserve; `missing` (no event) records
what restoration lacked. Search hits and packet events carry the latest verdict as
`agent_feedback`; `memory_status.agent_feedback` counts verdicts and lists recent
`missing` notes. Feedback is derived metadata: evidence is never edited, and an
agent can be wrong, so its verdicts annotate rather than delete.

### Selection, dates and corrections

Policy 8 uses the same decoded tool-output views for recent restoration and older
tree previews as for search. Equivalent transport copies share a packet slot;
different feedback remains separate. Retrieval-only calls and collaboration-mode
scaffolding are omitted from restoration, while raw event zoom stays exact.
Versioned `tool_output_views` cache decoded text in SQLite across processes, avoiding
repeated parsing. Catalog 7 recognizes printed Python search hits and diagnostic
replay batches; mixed new failures remain evidence. These are presentation rules,
not a general classifier for arbitrary copied prose or code.

Restoration favors literal topic matches from recent substantive user messages,
current-session notes, and explicitly marked durable preferences. Relevance comes
before pin status. Other chats' events stay available through project-scoped
search but are not automatically injected by a native session's hook. Manual
`pack` and `memory_checkpoint` accept `session` and `focus` overrides; omitting
session retains the logical project's combined history. Lexical focus is a
heuristic, not semantic understanding. A bounded reserve of the last 16 older
user messages helps preserve decisions when the topic's vocabulary changes.

Explicit `expires_at` values and a six-hour expiry for narrow operational-status
patterns keep stale process/restart reports out of automatic restoration. Missing
dates on status are labeled unverified; recent status remains unverified until
checked. Explicit `memory_kind: preference` notes do not expire merely because
their text mentions a process, though an explicit expiry still applies. Ordinary
historical evidence has no automatic expiry. Regex detection can miss status or
misclassify mixed prose; all omitted evidence remains on disk and searchable.

Structured claims with `key`, `value` and explicit `corrects: true` resolve a
conflict only when the correction is strictly newer. A narrow restart-verification
rule also works within a known session. Other contradictory values remain marked
unresolved. Resolution records retain both source note IDs; short dedicated
corrected status notes can be omitted from presentation without deleting them.
Multi-fact prose is retained. Zoom accepts `note:<id>` for the exact original note,
including superseded/retracted records. These are evidence rules, not permission.

## Retrieval and storage

Approval-review transcript copies, recognized snapshot-worker summaries, and
replayed search/zoom/status results are excluded
from candidate ranking and packed history. They remain immutable and accessible by
event ID. Derived metadata is rebuilt on upgrade; no source records are deleted.
Repeated complete tool documents are grouped before paid ranking after removing
known transport envelopes. Indentation, literal backslashes, changed document
versions, exit statuses, errors and other payload fields stay distinct. Identical
complete user/assistant text on different dates shares a search slot, with a
duplicate count and bounded dated source pointers. Original dates, feedback
identities, and restoration semantics stay independent; different feedback
verdicts prevent search grouping. Retrieval-only calls are omitted from search
but remain in the event stream and accessible by zoom. Mixed action batches
remain searchable. Ordinary attempted calls rank after source evidence; decision
questions also prefer direct user/assistant statements before tool output.
Lexical search ignores common question words and streams matching windows before
the 24-candidate limit, so repeated prompts cannot exhaust a window shortlist.
Work scales with matching archive content; this finite result shortlist can still
miss relevant evidence. No model calls are needed for these local policies.

Catalog 6 decodes known nested tool transports for search presentation and removes
recognized copied search results, startup references and diagnostic event replays
within each batch. Fresh errors and test results in the same batch remain visible.
Decoded results carry an explicit `presentation` label: their `offset: 0` points
to the complete raw event, not to a character in the displayed excerpt. Matching
decoded tool documents share a slot and retain bounded pointers to their copies.
Unknown or damaged transport formats remain visible; this is not a guarantee
that all generated text or truncated logs are identified as copies.

Older history uses cached binary-tree literal excerpts, not generative summaries.
Each node keeps 4 excerpts and widens to 8, 16 or 32 while the whole tree still fits
the packet budget. They may omit important facts. Search returns original windows
or labeled decoded tool excerpts; zoom
returns exact character pages and `next_offset`. Large recent tool outputs use
literal head/error/result/tail excerpts plus an exact zoom pointer; they are not
promised verbatim within the 8k allowance. Replayed native events collapse while every
source occurrence/raw public provenance remains stored. Resumed and forked sessions
replay earlier turns under new session IDs; older history shows each such document
once, and omits tree previews of events it already shows in full. Harness injections
(AGENTS.md, skill bodies, environment context, aborted-turn and multi-agent notices,
Connectome checkpoint prompts) are scaffolding. Base64 images are presented as
digests. Generated memory packets
are excluded from repacking/search; superseded/retracted notes do not become current.

```powershell
uv run memory-tool search --chat my-master 'earlier decision'
uv run memory-tool zoom --chat my-master 42 --offset 0
uv run memory-tool zoom --chat my-master note:example-note-id
uv run memory-tool pack --chat my-master --session native-thread-id --focus 'retrieval recovery' --output C:/private/history.txt
```

`--jev` optionally ranks the first 12 distinct redacted source candidates directly
with TypeSafe, from a local shortlist of up to 24. As in gpt-researcher's Jev
context filter, each passage is scored alone (passages sharing a request shift
scores onto neighbours) and ranked passages below 1.5 of 3 are dropped; the response
lists them in `below_threshold` for zoom. Unranked candidates remain available after
ranked results. Credentials
come from `TYPESAFE_API_KEY` or its existing Windows user environment value. At most
12 concurrent requests per retrieval, four-second HTTP timeouts, 24-hour exact-input cache,
no automatic retries, local fallback. Redaction is best-effort; enabling Jev exports
candidate conversation excerpts. Plain retrieval and packing stay local.

### Shared lasting notes and tree navigation

Native packing and search index the current project's `md_archive/*.md` (including
subdirectories). Register additional directories or legacy Connectome JSONL notes
explicitly inside that project:

```bash
memory-tool index-notes --chat home --source /home/hek/personal-tools/memory-tool/md_archive
memory-tool index-notes --chat home --source /home/hek/.codex/connectome/notes.jsonl
```

Registered sources refresh locally on search, packing and Stop. Markdown becomes
literal section snapshots with file path, revision hash, line and character
offset. Changed or removed files update the current document view; old notes and
tree references stay zoomable. Source files are never edited. Legacy `{ts,type,text}`
notes lacking IDs/project metadata can be imported into the explicitly chosen
chat; they are not automatically copied into every project.

`memory_note` writes concise project notes with optional event/note/tree source
references. Explicit `supersedes` IDs retire prior notes from current selection;
independent conflicting notes remain separate. Note feedback supports `note:<id>`.
Ordinary search now reserves shortlist space for notes alongside events. Note
excerpts point to exact JSON records through `memory_zoom`; native restoration
also supplies a balanced `tree:knowledge-…` root with children leading to notes.
These are current-note trees, separate from the proxy's chronological summary tree.

With `serve --jev` or `search --jev`, sparse keyword note matches trigger bounded
Jev-guided note-tree traversal: at most 16 branch judgments and 12 final passage
judgments by default, cached for 24 hours. Branches are navigation hints, never
answers; returned hits refer to source notes/events. Literal search remains
available if summaries miss a concept, the call budget runs out or Jev fails.
This adds concept-based recall without claiming complete semantic coverage.

Imports are bounded to 128 Markdown files, 256 KiB per file, 4 MiB total, and
1 MiB per registered legacy JSONL. Symlinks outside the source/project are skipped.
Scan failures/limits are reported; an incomplete scan never retires missing files.
Hooks and packing do not call models synchronously. Jev uses the environment key
or saved **direct TypeSafe** Jevgrep credential (custom/gateway credentials are
excluded). Enabling ranking/background labeling sends redacted excerpts to
`api.typesafe.ai`; `MEMORY_TOOL_AUTO_LABEL=0` disables background labeling.

This stage does not automatically extract facts from all conversations or resolve
semantic contradictions. File revisions and explicit corrections are supported;
autonomous reconciliation and topic embeddings remain future work.

### Named label vectors

`memory-tool label --chat my-master [--limit 2000]` asks Jev eight `noul`
questions about each unique document (notes first, then newest events): `decision`, `user_constraint`,
`outcome`, `failure`, `plan`, `transient_status`, `routine`, `durable`. Each probability has a
name, so selection can say why it kept an event. Vectors are cached per duplicate
key, so replayed copies cost nothing. Labeling is automatic: when unlabeled documents
exist and a TypeSafe key is available, the `Stop` hook starts a detached labeler
(up to 500 documents per run) and returns without waiting. A lock file beside the
archive allows one labeler at a time; output goes to `labels.log`. Set
`MEMORY_TOOL_AUTO_LABEL=0` to disable. Hooks and packing only read the cache and
make no network calls themselves. Unlabeled events keep keyword selection.

Label policy 3 favors reusable findings, causal explanations and lasting decisions.
Routine and transient scores discount plans, outcomes, failures and generic
decisions; constraints and durable findings retain their score in mixed prose.
Older history and notes below 0.25 are omitted from restoration. Routine event
matches also rank later in local search. Tree excerpts rank by this importance
without reserving slots merely for the first/last event. Assistant/user turns with `decision` or
`user_constraint` of at least 0.5 join the continuity reserve. On an 83-session
private archive, labeling 4,084 documents took 77 s and 3.1M input tokens (about
$0.13). Older-history mean importance rose from 0.69 to 0.81, with no excerpts
below 0.25 (13 before). The labels are model judgments, not facts.

For decision questions, presentation adds 0.12 to ranked direct user/assistant
statements and subtracts 0.12 from attempted tool calls. Returned Jev scores stay
unadjusted and the response labels this policy. This favors direct explanations
without asserting that an assistant statement is true or a tool call succeeded.

Store: `~/.local/share/memory-tool/memory.sqlite`, or `MEMORY_TOOL_HOME` / `--db`.
Use SQLite-aware backups: copying only the main file while WAL is active is insufficient.
Raw public records are immutable; hidden reasoning is excluded by gateway ingestion.
Never commit private archives, transcripts or keys.

This follows [OptMem's](https://github.com/VictorTaelin/OptMem) binary-tree/external-history
philosophy with Connectome's scoped notes and source evidence. It does not prove
perfect lifetime recall: FTS can miss paraphrases, previews lose detail, and packing
currently scans event metadata. Tested at 10k synthetic events, not hundreds of millions.
Claude native capture/recovery has offline coverage; live installation and model
receipt remain unverified. The older Claude gateway is separate from native hooks.

## Verify

```powershell
uv sync --group dev
uv run pytest -q
uv run ruff check src tests scripts evaluations
uv run python scripts/live_codex.py
uv run python scripts/cache_compare.py
uv run python scripts/live_native_hooks.py
```

Live probes consume the installed Codex account's usage. The cache comparison uses
matched prompts/seeds, but one sequential trial per policy is only a smoke comparison.
The native hook probe requires the two memory-tool hooks to be trusted normally;
otherwise it exits with `needs_hook_trust` and makes no model calls.
See [VALIDATION.md](VALIDATION.md) for observations and limits.
The fixed [12k/24k/64k evaluation](evaluations/RESULTS-20261004.md) records raw
answers, strict scores, retrieval calls, token usage and latency, including failed
development iterations. It supports an initial default, not an optimal budget or
perfect lifetime recall claim.
