# Validation: 2026-10-04

## Version 0.2 follow-up

32 automated tests passed after adding retrieval source classification, distinct
candidate selection, project routing, transcript capture and native lifecycle hooks.
Tests cover approval-review and retrieval echoes, preservation of changed dated
facts and exact zoom, invalidation of stale tree caches, bounded paid ranking,
partial transcript lines, session/project mismatch, hidden-reasoning exclusion,
retracted notes on restoration, additive/idempotent hook installation and unchanged
hook trust records. Ruff F checks passed.

After the user completed normal Codex hook trust review, a live native test passed:
an authenticated disposable Codex thread first answered READY; a random marker was
then written only to its isolated external archive. Codex performed native
compaction, the compact SessionStart hook prepared restored memory, and the next
model turn returned that unseen marker exactly. No manual `thread/inject_items`
call was used. The test archive/workspace was cleaned up afterward. This verifies
native hook execution and model receipt on an independently owned Codex server;
it does not claim this existing desktop chat was compacted during the test.

Native context targets are configured at 160k automatic compaction / 200k context.
Both new hooks and all five existing hooks were verified through `hooks/list`.
The MCP registration uses a separate versioned Windows runtime because upgrading
the active uv environment in place fails on locked executable files. Existing
clients need an MCP reload to use the updated retrieval process. New terminal
sessions resolve the versioned command through the user PATH.

The final installed runtime also passed a fresh native MCP probe: Codex discovered
all four tools, called status and search, and recovered the original spending-cap
message at event 216. This probe used local ranking. Its audit reads raw public
events, because successful retrieval results are deliberately absent from the
filtered source view. Both hook approvals remained trusted after installation.

On the previously failing real question about the original provider/spending cap,
the original user message (event 216) ranked first. The first v0.2 probe used three
Jev requests and 5,739 reported input tokens, compared with six and 10,621 in the
preceding v0.1 test. This is one selected regression, not an independent quality eval.
The snapshot contained 226 approval-review transcript replays. These were copies
of earlier conversation presented to approval reviewers, not fresh human messages.
One additional escaped retrieval format was identified and added to the filter
after this probe. Raw records remain intact.

The Windows desktop has no default control-socket directory. Native recovery now
uses documented `PreCompact` and `SessionStart` hooks. Direct hook/unit checks
verify prepared bounded context; the live receipt test above additionally verifies
normal trust review and native compaction. Do not equate a prepared packet
with model receipt. Existing native summaries/other hook output remain additional.

The following sections record the original 0.1 validation rather than claiming
all live probes were rerun after this follow-up.

Windows, Python 3.13.9 through uv, tiktoken 0.14.0, authenticated Codex
`gpt-5.6-luna`. Global model/permission settings were preserved. Codex work is the
current supported scope; the user explicitly deferred Claude.

## Automated checks

22 tests passed. Coverage includes immutable records/restart, project isolation,
10,000-event history, bounded packets with metadata, exact Unicode zoom across
large events, search at an event's tail, replay deduplication/raw provenance,
superseded notes, capture-backlog refusal, fresh-context recovery construction,
adaptive reuse/rollover, no backend start after archive failure, waiting for real
compaction completion, public tool output retention, Jev redaction/cache/fallback,
offline tokenizer equivalence, and MCP transport under a cp1252 pipe environment.

The offline o200k asset is compressed, checksummed and retains tiktoken's MIT
license. This removes the encoder's first-use network dependency. The new Unicode
transport test addresses an observed native MCP stall: metadata calls worked, but
search hung until pipes used UTF-8 and wire JSON escaped non-ASCII safely.

## Live Codex checks

- A source fact deliberately absent from a 1,500-token packet was recovered with
  `memory_search` and verified with `memory_zoom` in a fresh disposable context.
- Closing/reopening the database and starting a different backend context preserved
  recovery. Final before/after packet sizes were 1,452 / 1,452 tokens.
- The master delegated reading a synthetic project file to one read-only worker.
- An opt-in write worker created a regular UTF-8 file in a disposable workspace;
  the host read back the exact contents. Initial temporary-directory probes failed:
  Python 3.13's Windows `mkdir(0o700)` applies OWNER RIGHTS ACLs, so new files owned
  by the sandbox worker were not readable by the host user. ACL inspection confirmed
  the difference from ordinary inherited workspace permissions. Probes now create
  and delete their own normal workspace directories; existing ACLs are never changed.
  See [Python's documented Windows behavior](https://docs.python.org/3/library/os.html#os.mkdir).
  The server also exits through stdin EOF/graceful cleanup before termination fallback.
- Supported native compaction completed on a disposable independently owned server;
  packet injection and subsequent exact marker recall both succeeded. Acknowledgment
  alone was not treated as completion. Native compaction keeps a summary.
- Native Codex discovered the registered `memory_tool` server and called both
  `memory_status` and `memory_search`, recovering the earlier $5 spending cap with
  a source event ID. The source was historical evidence, not new authorization.
- Real project history was imported locally: the initial snapshot produced 1,318
  visible semantic events from 2,466 source occurrences. Its packed history used
  18,847 tokens, including 7,325 recent tokens. Counts grow as native capture continues.
- Real Jev retrieval ranked 24 candidate windows in six requests / 10,360 reported
  input tokens. This is a functionality check, not an independent retrieval-quality eval.
- Configured Jevgrep completed a scoped active-source query with exit 0 and `End context.`

## Small cache comparison

Matched seed 9371, four identical prompts per policy, identical initial synthetic
history, 6,400-token history budget / 2,000 recent tokens. All 12 answers preserved
the chosen label.

| Policy | Reused turns | Input tokens | Cached input tokens | Total seconds |
|---|---:|---:|---:|---:|
| Adaptive, 160k threshold | 3 | 38,469 | 17,920 | 7.551 |
| Fresh every turn | 0 | 38,617 | 0 | 13.297 |
| Adaptive, 10k threshold | 2 | 38,543 | 8,960 | 10.177 |

Counts are differences of provider-reported cumulative usage, avoiding double
counting notifications. Early rollover created a second context before turn three.
Default reuse is justified by this observation; the particular threshold is a
conservative initial choice, not an optimum measured by this experiment.

One sequential trial per policy, shared provider cache, short answers, and ordering
confound latency. This does not establish billing savings or behavior at 200k.
The comparison preceded the subsequent shutdown/transport fixes; its timings are
observations of that prototype rather than a performance guarantee for the release.
No GPU/cloud instances were launched for this work.

## Remaining boundaries

The installed desktop's CLI control socket is unreachable (Windows error 10050).
Remote clearing of this existing desktop chat is **not verified**. Gateway resets
are verified and native MCP retrieval is verified; the existing chat may require
MCP reconnection to expose a newly registered server. Existing Connectome hooks
were preserved.

64k refers to the packet's named encoder. The 200k context target relies on local
estimates, reported model usage and native compaction; it is not a universal hard
cap. Literal previews and lexical shortlisting cannot guarantee lifetime recall.
Native public capture depends on the existing Connectome observer/hooks. Private
data and real transcript excerpts are outside this repository.
