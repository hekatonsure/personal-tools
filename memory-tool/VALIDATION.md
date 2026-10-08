# Validation

## Activated — 2026-10-08: task scope and ranked-only search

Selection policy 9 / label policy 4 adds Jev `general_preference`: a standing
user preference that applies beyond one task. Before a task is known, startup
outside a Git repository injects only notes above the 0.5 general-preference
threshold, within a 1,200-token note budget. Repository startup retains project
notes. Focused packets no longer treat every declared preference as universally
relevant. All active notes remain reachable through the packet's note-tree root.

Successful Jev search now returns only scored, threshold-passing candidates.
`unranked_omitted` reports the rest of the bounded shortlist. Provider failures
retain explicitly marked local retrieval. Original events, notes, feedback and
provenance remain unchanged.

- **264 tests passed**, plus Ruff on `src`, `tests`, `scripts` and diff checks.
  New regressions cover home/repository startup, omitted-note tree reachability,
  task-specific preferences, all-weak scores, bounded requests, cache reuse and
  provider failure. These extend the prior 260-test suite.
- **13 live scope examples passed**, including five held-out examples. An initial
  0.8 cutoff rejected valid general preferences; a clearer question and a 0.5
  cutoff separated general examples (0.60–0.80) from task-specific rules,
  technical findings and progress reports (0.03–0.13). This small diagnostic set
  is not a comprehensive classifier benchmark.
- A private SQLite backup received **3,695 updated labels** (3,333,645 reported
  input tokens). With identical 24k/8k packet budgets, unfocused startup fell
  from **9,540 tokens / 24 notes to 2,678 tokens / 4 notes**. Three queries retained
  their top evidence: completed machine cleanup, SQLite locking cause, and the
  Taj arm-position constraint. Their unranked hit counts fell from **5/3/4 to
  0/0/0**, with candidate query times **0.25–0.44 s**. The robot rule remained
  retrievable despite being excluded from unrelated startup context.
- Fingerprints of events, provenance, notes and feedback matched before/after
  replay; SQLite integrity was `ok`. The packaged runtime's 24 Python files
  matched the tested source, with the previous runtime's exact dependencies.
- Activated `runtimes/20261008-relevance`, imported only derived labels into the
  live archive, and retargeted the ordinary launcher and MCP registration.
  The daemon accepted MCP reload; this chat's actual MCP connection then reported
  policy 9. Two live searches retained the correct top results with **zero
  unranked hits**. The ordinary launcher produced a **2,678-token** startup packet.
  No fresh native compaction/receipt test is claimed.

Private evidence and rollback files are in
`~/.local/share/memory-tool/deployments/20261008-relevance/`: evaluation script,
before/after JSON and packets, exact dependencies, wheel, activation database
backup, previous launcher module/config and reload receipt. Restore the previous
launcher module and MCP path to roll back; do not overwrite a live archive with
an old backup. Proxy and hook definitions were not changed.

Remaining limits: semantic scope classification can misjudge ambiguous or mixed
notes, and near-duplicate general preferences may still occupy separate slots.
Focused packet selection remains local; semantic query ranking and note-tree
navigation provide deeper recall on demand.

## Active salience scoring fix — 2026-10-08, 21:11 UTC

The standing authorization now names memory-tool in both Codex AGENTS.md and
Claude CLAUDE.md. Automatic review accepted the minimal addition to the existing
named-tool list; no further authorization is pending.

A live Jev probe found routine next-action prose scored 0.60 as a decision.
Label policy **3** narrows the decision question and discounts generic decisions,
outcomes and plans by both routine and transient scores. Durable findings and
user constraints retain their score. Eight live examples passed after the fix.
A separate live tree probe recovered the WAL note with no lexical matches using
six branch judgments plus one source judgment (3,301 input tokens).

**260 tests passed**, Ruff and diff checks passed. Private-copy Jev validation
labeled 100 records, preserved original event fingerprints, and returned source
hits for three queries in 0.29–0.38 s. These are focused probes, not comprehensive
recall/precision benchmarks. The live archive then received 800 policy-3 labels;
2,630 records remained for bounded background batches at that check.

Current ordinary launcher and MCP registration use
`runtimes/20261008-salience` with `--jev`. This package preserves the other
session's startup refinements; only selection.py and labels.py differ from
`20261008-startup-salience`. Fresh MCP search used Jev and exposed all six tools.
The daemon accepted reload, process inspection showed both MCP servers on the new
runtime, and this chat's MCP status verified policy 8, shared note search and Jev
tree navigation. No user restart is required for the checked connections.

Evidence/rollback: `deployments/20261008-salience/` contains `bridge.json`,
`previous-cli.py`, `previous-config.toml`, `activation-before.sqlite`,
`live-validation.json`, `live-branch-probe.json`, `activated-mcp.json`, and
`reload.json`. Restore the previous bridge/MCP path for rollback; never replace
the live archive with an older database. Proxy and hook definitions were preserved.
Earlier deployment sections below are dated historical checkpoints.

## Activated — 2026-10-08

At the user's explicit request, activated the previously tested
`20261008-startup-salience` runtime. The ordinary launcher now dispatches there;
Codex's `memory_tool` MCP registration also points directly to that runtime.
The daemon accepted `config/mcpServer/reload`. A subsequent call through this
chat's actual MCP connection reported policy 8 and 512 indexed notes, confirming
the live connection switched. A 6k-budget restore check selected five notes
and used 3,670 tokens.

Fresh MCP smoke verification through the ordinary launcher exposed all six tools,
reported selection policy 8, and returned nine search hits including five notes.
Live indexing registered the repository's `md_archive` and Connectome `notes.jsonl`;
both scans completed without errors. Packaged Python sources still match the
260-test checkout. After the user explicitly extended standing authorization to
memory-tool's private-memory TypeSafe requests, enabled MCP `--jev` and automatic
background labeling. This chat's actual MCP status now reports
`jev_tree_navigation: true`; a live query returned `mode: jev`, no error, 11 hits,
12 provider requests and 7,463 reported input tokens. A backup query also passed
(12 requests / 7,319 input tokens). These verify operation, not comprehensive
ranking quality; an observed top hit described the symptom rather than its cause.
Initial labeling processed 600 records (494,485 reported input tokens), leaving
2,764 for subsequent bounded background batches. The temporary local-only guard
was removed after the explicit authorization; earlier review rejections are resolved.

Rollback artifacts in `deployments/20261008-startup-salience`: `previous-cli.py`,
`bridge.json`, `config-before.toml`, `pre-activation.sqlite`, and `reload.json`.
`config-before-jev.toml` preserves the intermediate local-ranking registration.
To roll back, restore the prior launcher bridge and the previous MCP executable
path, then request reload; do not replace the live archive with the backup.
The HTTP proxy and hook definitions were not changed.

## Startup findings and short corrections — 2026-10-08

Startup reply selection no longer drops a short final correction in favor of an
older long result. Documentation handoffs keep their latest literal reply and
can include a separately dated preceding substantive reply. This is a local
presentation heuristic, not semantic reconciliation or proof of current status.

- **260 tests passed**, including two new regressions; Ruff and diff checks pass.
- Local SQLite backup replay recovered event **6379** alongside documentation
  handoff **6403**: the former records the unresolved diagnostic echoes and
  cluttered restored context that the latter omitted. All five session references
  survived; packet size increased from **3,408 to 3,525 tokens**. Original event
  fingerprints matched and SQLite integrity was `ok`.
- Source changes are staged for deployment with the previous shared-note work.
  Live TypeSafe ranking and launcher activation remain pending the explicit
  private-memory export authorization requested in this session. The existing
  runtime and proxy were not changed.
- Built an offline wheel and isolated runtime at
  `~/.local/share/memory-tool/runtimes/20261008-startup-salience/`. Packaged Python
  sources match the tested checkout and its launcher reproduced the 3,525-token
  replay packet. The ordinary launcher still points to `20261008-db-lock`.

Private replay script, SQLite copy, before/after packets and measured results:
`~/.local/share/memory-tool/deployments/20261008-startup-salience/`.

## Shared note salience — 2026-10-08

Implemented shared note/event search, immutable Markdown section snapshots with
revision heads, explicit legacy note imports, native `memory_note`, source-linked
`note:` / `tree:knowledge-` zoom, note feedback and expiry, durable Jev label v3,
routine-result demotion, and bounded Jev note-tree navigation when lexical matches
are sparse. Existing chronological proxy summaries remain separate. Autonomous
fact extraction and semantic contradiction reconciliation are not implemented.

- Baseline: **247 tests passed** with loopback socket access. Updated suite:
  **260 tests passed**; Ruff and diff checks passed. Tests cover revised/deleted
  files, old-tree/source recovery, project isolation, unscoped legacy imports,
  explicit corrections, conflicting notes, expiry/feedback, sparse lexical recall
  through mocked Jev traversal, cache reuse and provider failure fallback.
- Local replay of a SQLite-aware live-archive backup imported **508 current
  notes** from this repository's `md_archive` and explicitly registered legacy
  Connectome notes into the home chat. A 6k-budget new-session packet focused on
  memory/retrieval selected five notes and used **3,560 tokens**; build time
  **0.39 s**. Three local queries returned 9–10 hits in **0.05–0.16 s**; each hit
  zoomed successfully. Original event fingerprints were unchanged; SQLite
  integrity check returned `ok`. These are integration checks, not measured
  semantic-recall improvements.
- An isolated installed wheel exposed all six MCP tools and returned ten local
  search hits. Runtime staged at
  `~/.local/share/memory-tool/runtimes/20261008-salience` with locked dependencies.
  At this checkpoint the ordinary launcher still uses the previous runtime.
- At that earlier checkpoint, live Jev evaluation and activation were pending approval (now resolved above):
  automatic approval review rejected sending private archive excerpts directly
  to TypeSafe, distinguishing this from the standing `jg`/`session-search`
  authorization. No private archive export was performed by this evaluation.

Private backup, replay script/results, restored packet, wheel and MCP smoke results:
`~/.local/share/memory-tool/deployments/20261008-salience/`.
Pre-change checkout backup: `/tmp/memory-salience-before/checkout.tgz`.

## Concurrent search and credential repair: 2026-10-08

Live MCP search and feedback returned `database is locked`, while exact zoom
worked. SQLite integrity was `ok` and another connection could acquire the writer
lock. A deterministic two-connection test reproduced the failure: search kept an
FTS cursor/read snapshot open, another writer committed, then a decoded-output
cache miss tried to upgrade the stale snapshot. Increasing the busy timeout would
not resolve this WAL snapshot conflict. Failed lazy writes also left a transaction
open on the long-lived MCP connection.

Search now fetches its rows before lazy cache writes. Standalone cache writes use
a transaction context that commits on success and rolls back on failure; existing
append transactions retain ownership. Both new regressions failed before the fix
and pass afterward. **247 tests passed**, plus Ruff and diff checks.

A SQLite backup of the real archive passed three search replays with **1,863
interleaved commits** from a second connection after clearing only its derived
tool-output cache. Events, provenance, notes and feedback fingerprints remained
unchanged; integrity stayed `ok`, and packaged Python sources match the checkout.
The normal installed launcher successfully searched the live database. This
chat's MCP search and feedback subsequently succeeded too.

Installed runtime: `~/.local/share/memory-tool/runtimes/20261008-db-lock/`.
Evidence, archive snapshot, exact locked dependencies, wheel, `validation.json`,
and rollback `previous-cli.py`/`bridge.json` are in the matching `deployments/`
directory. Restore the previous bridge to the recorded `old_entry_module` to
roll back routing; never replace the live archive with the snapshot. The proxy
continues on its original runtime. Hook definitions and Codex config are unchanged.
The current Codex daemon accepted `config/mcpServer/reload`; refresh is queued
for the next turn. The current turn still used the prior MCP process, recovered
after cache warming, so its successful calls alone do not prove the new runtime
was loaded in that connection.

The separate `session-search` tool now falls back from `TYPESAFE_API_KEY` to
Jevgrep's saved **direct TypeSafe** credential, respecting XDG config location.
It rejects credentials for other providers and reports setup errors without
printing secrets. Its editable installation immediately picked up the fix:
**22 tests passed**, and a fresh real search made 13 authenticated requests
(48,498 input tokens, reported $0.0020). The existing key was reused without
copying it into shell configuration. README and linked skill instructions match.

## Restored packets and cached tool views: 2026-10-08

Policy 7 / catalog 7 applies decoded tool-output presentation to recent events
and older tree previews, groups equivalent transport copies without combining
different feedback, and omits retrieval-only calls and collaboration scaffolding
from restored packets. Printed Python hits, query/tuple replay batches and
raw/view-length debug dumps no longer replay their copied answers. Fresh failures,
including traceback/exception boundaries within a diagnostic leaf, survive.
Expiry now evaluates the cleaned result: copied old process status cannot expire
an unrelated failure in the same event. Exact originals remain available by zoom.

Versioned SQLite `tool_output_views` persist decoded text across searches and
process restarts. Missing entries from older writers are populated lazily. The
cache does not change stored duplicate identities, labels or feedback, and writes
participate in an enclosing append transaction. Catalog migration invalidates
old derived tree excerpts; the selection policy also changes their cache identity.

Baseline **238 tests passed**; final **245 tests passed**, with seven additional
regressions covering diagnostic filtering, recent/tree presentation and grouping,
independent feedback, restart reuse, old-writer backfill, version invalidation,
transaction rollback and status expiry. Full tests used local loopback permission.
Ruff and diff checks passed. Installed Python sources match the tested checkout.

A fixed SQLite backup replayed four queries and the prior session's 24k/8k
restored packet. Event/provenance/note/feedback fingerprints stayed unchanged.
The `bc9bcaf` search no longer returns diagnostic copies 6296, 6230 or 6210;
6011/5930 remain, as do summary-tree implementation 1121 and spreadsheet outcome
1742 in their queries. Event 6210 retains its fresh AssertionError and 6296 its
fresh test success. The restored packet shrank from **20,698 to 14,665 tokens**,
with `chunk_id` occurrences falling from **54 to zero**. Four warm searches took
1.50 seconds before, 0.66 seconds in the checkout and 0.40 seconds in the package.
The one-time migration/cache fill took 1.06–1.97 seconds; reopening the packaged
archive took 0.004 seconds. These are local samples, not a general latency or
retrieval-quality benchmark. Packet construction remained below 0.25 seconds.

Installed runtime: `~/.local/share/memory-tool/runtimes/20261008-packet-noise/`.
The ordinary launcher passed search and packet smoke checks through the bridge.
Evidence, wheel, backup and `previous-cli.py` for rollback are in
`~/.local/share/memory-tool/deployments/20261008-packet-noise/`. Restore that bridge
file to `old_entry_module` in `bridge.json` to retarget new processes to the prior
runtime; do not restore the old archive over newer records. Existing MCP processes
need reconnect. The running proxy, hooks, trust records and Codex config were
not changed. Exact locked dependencies came from local cache; their existing
September/October versions required overriding uv's older exclude-newer cutoff.

Remaining limits: arbitrary prose/code and differently shaped diagnostic reports
can still rank as source evidence (including copied reports in 6365); this is not
complete semantic deduplication. Fresh code listings may outrank direct answers
for exact-token queries. Startup can still select a final archive-note update
instead of the preceding outcome/remaining-work reply; missing feedback records
that observation. No new live native-compaction receipt test is claimed.

## Nested tool-output search views: 2026-10-08

The previous noise fix was incomplete. Live search still surfaced startup probe
references, escaped native/proxy copies of the same command output, and nested
session-search results. Catalog 6 classifies recognized copied leaves separately
from fresh results in the same batch. Search decodes known tool transports,
retains exit/error metadata, groups equivalent decoded outputs and labels the
displayed excerpt separately from its exact raw-event pointer. Raw events,
provenance, notes, feedback and zoom bytes stay unchanged.

**238 tests passed**, including nine new tests for mixed startup probes,
native/proxy duplicates, fresh failures beside retrieval echoes, code discussing
the schema, status and literal backslash preservation, catalog upgrades, printed
diagnostic replays, session-search results following progress, JSONL transports,
and malformed blocks. Ruff and diff checks pass. Socket tests require loopback
access; the sandbox-only full run failed at socket creation before exercising
those tests. The final full run with loopback access passed.

Three queries were replayed against an independent SQLite backup with the old
runtime and the packaged candidate. The original latest installation answer,
summary-tree implementation and spreadsheet archive result remain retrievable.
Events 6011/5929 now group into one readable command result. Startup/reference
and diagnostic copies are omitted within mixed batches while fresh source
listings and failures remain. Immutable tables compare byte for byte. This
sample took 0.763 seconds on the old runtime and 3.394 seconds on the final
package, including archive open/reclassification and all three searches; decoding
adds work, and this is not a general latency benchmark. Unknown/damaged wrappers
and arbitrary generated prose may still appear.

Installed runtime: `~/.local/share/memory-tool/runtimes/20261008-tool-output/`.
The normal launcher passed a search smoke check through the updated bridge.
Evidence, wheel, archive backup and previous bridge are in
`~/.local/share/memory-tool/deployments/20261008-tool-output/`. Existing MCP
processes need reconnect/restart; new launches use the fix. The proxy service,
hook definitions and Codex config were not changed by this runtime upgrade.

The separate session-search stall was sandbox network access: with explicitly
authorized provider access its real query ranked in 0.41 seconds, after a
0.68-second index update. No session-search code changed. The user explicitly
approved permanent `jg`/`session-search` access, including queries and relevant
source/session excerpts to `api.typesafe.ai`. Global prefix rules and AGENTS.md
authorization were installed and verified; permission-file backups are in
`~/.codex/backups/search-permissions-20261008T173501Z/`.

## Search repetition and startup outcomes: 2026-10-07

Checkpointed all preceding memory work as `450f12f` before editing. Baseline:
**220 tests passed** with local socket access. Policy 6 / catalog 5 groups identical
dated text only in search presentation, excludes retrieval-only requests from
search (while preserving the original event stream), recognizes Connectome's
exact snapshot JSON schema and status echoes, and prefers direct statements for
decision questions. Matching windows are streamed before applying the distinct
candidate limit, removing the duplicate-window starvation seen in this chat.

Startup now includes literal replies outside the current repo, groups repeated
opening requests within their original project, prefers recorded final answers,
and can include a separately attributed reply from a fuller related conversation.
Disposable installation probes fall behind ordinary work. General orientation
may use 2.4k tokens within the existing 4k startup/whole-packet budgets. Dates,
feedback, original scopes and exact zoom references are preserved.

**229 tests passed**, including nine new regressions covering 850 repeated
dated prompts, changed document tails, independent feedback, retrieval echoes and
mixed action batches, snapshot-schema upgrades, local decision ranking, topic
diversity, complete topic identity, final replies, fuller conversations and
idempotent native transcript replay after snapshot reclassification. The same
suite passes against the installed candidate package.
Ruff passes for `src tests scripts`; repository-wide Ruff still reports 30
pre-existing formatting violations in `evaluations/summary_models.py`.

Real-archive replay used independent SQLite backups and both the previous runtime
and the packaged candidate. The tree query previously returned search-call echoes
and repeated continuation prompts; it now returns the original implementation
and validation statements. Fifty-two tree-topic sessions occupy one startup slot,
leaving room for hook and spreadsheet context. The observed startup grew from
862 to 1,628 tokens. Events, provenance, notes and feedback compare byte for byte
before and after derived-catalog rebuilding. Replay took about one second per
runtime; this is a smoke check, not a latency benchmark or semantic-quality eval.

Deployment evidence, wheel, replay script/results and the previous bridge are in
`~/.local/share/memory-tool/deployments/20261007-retrieval/`. The separate runtime
uses the existing launcher bridge for new invocations. Existing MCP processes
retain their loaded version until reconnect. No proxy restart, hook re-enable,
trust/config edits or source-event migration are required.

Known limits: reply selection and topic grouping are local heuristics. They do
not infer which conflicting statement is true. Search work scales with matching
archive content; arbitrary generated prose and mixed transcript/log outputs may
still appear. The unrelated session-search network stall was not changed here.

## Repository-first startup recall: 2026-10-07

Diagnosed an empty live startup packet: 584 source events existed in other home
sessions, but selection filtered all of them out before ranking and there were no
curated notes. Policy 5 adds bounded startup orientation for empty conversations:
repository history first, then a small cross-project session directory with exact
retrieval scopes. Existing session restoration and original archive bindings stay
intact. Repository discovery asks Git; sandbox-created empty `.git` directories
must not be mistaken for repositories.

**220 tests passed**, including seven new regressions covering repository/subdir
grouping, general references, established-session isolation, feedback/expiry,
ambiguous logical chats, byte-preserving originals, chronological ordering, small
packet budgets, nested repos, worktrees and empty Git guards. The initial full run
had 27 loopback socket permission failures; rerunning with local socket access
passed. Ruff and diff checks passed. Real-archive snapshot replay finds general
references from home, resolves memory-tool's parent repository, and selects robot
repo history before other sessions. Home-started memory-development sessions are
still correctly referenced under home, not silently assigned to personal-tools.

Installed a separate `20261007-recall` runtime and retargeted the existing launcher
bridge. Only `packing.py`, `selection.py` and new `orientation.py` differ from the
previous installed package. Before/after installed-hook replay on a SQLite backup
passed for home, a personal-tools subdirectory and rusty-robots; the actual bridged
launcher then returned policy 5 with repo history before general references.
Hooks, trust/config files and the running proxy service were not changed.
Evidence and bridge backup: `~/.local/share/memory-tool/deployments/20261007-recall/`.
New hook launches use the fix immediately; existing MCP processes retain their
loaded version until reconnect. No live archive migration was performed.

This verifies selection and hook output, not a new model-receipt or summary-quality
evaluation. Startup references only cover archived conversations; it does not
import all historical Codex/Claude sessions or infer a repo from arbitrary prose.

## Normal Codex installation and native routing: 2026-10-07

Installed and enabled after user authorization. A dated runtime, an unchanged
approved MCP/hook command bridged to that runtime, and a persistent user service
now support normal Codex use. The config selects the local Responses provider,
keeps `gpt-6-astra` / `high`, uses existing ChatGPT authentication, and disables
WebSockets for this provider. Configuration, hooks and the live SQLite archive
were backed up before activation; rollback preserves newly accumulated evidence.

The single-conversation proxy gained explicit `--codex-home` native routing. It
uses protocol UUIDs plus local thread/project records, persistent project bindings
and a bounded per-thread compactor cache. Unknown/conflicting identity passes
through without capture. `/responses/compact` passes through unchanged. Local
regressions cover session/project separation, changed project bindings, missing
identities, metadata disagreement, restart/cache behavior and HTTP forwarding.
**213 local tests passed; lint and diff checks clean.**

A live native-routing probe with `gpt-6-astra` passed all nine requests, actual
thread attribution, compaction, native MCP search/zoom and recovery of an omitted
marker. Then two fresh sessions using the installed global config and installed
MCP exercised the running service in different disposable projects: both called
`memory_status` successfully, all four requests returned HTTP 200, and all public
evidence was captured in the correct project. These installation probes complement
the medium-reasoning/model-summary stress run below; they do not establish a new
large-context or latency benchmark. The two installed-config test threads were
archived after completion.

Settings, evidence paths, operating commands and rollback are recorded in
[md_archive/codex-live-setup.md](md_archive/codex-live-setup.md). Existing active
threads are not switched mid-turn; restart/reconnect Codex to load this setup.

## Reasoning-aware Codex tree compaction: 2026-10-07

The tree now replaces complete older spans containing recognized OpenAI encrypted
reasoning. It cuts only at user-turn boundaries, verifies unique tool-call/result
pairs stay wholly inside the removed span, and requires model output after each
reasoning run before a user/result boundary. Unknown reasoning fields, malformed
payloads and unfinished steps still block replacement. The existing default keeps
three recent user turns; the live stress probe keeps one.

Old reasoning is removed with its associated steps, without decrypting it or
including it in the public archive or summary model. Public evidence remains
zoomable. The original prefix and recent suffix are preserved byte for byte,
including retained reasoning. Cached replacements recheck both the user boundary
and tool-pair closure. Claude thinking/signature blocks remain protected; this
change applies only to the known OpenAI Responses format.

This choice follows source inspection of Gobstopper commit
`2c6d33fd02e9ee1b1c2212bbd9a3847da5bce148`: its Responses adapter omits encrypted-only
reasoning from summaries and its compactor retains whole recent model steps.
Our implementation keeps the existing, more conservative user-turn boundaries
and the public-only memory archive. Gobstopper was inspected, not live-tested.

Local validation: **202 tests passed**, Ruff and `git diff --check` clean. New
regressions cover old/recent reasoning, public-only summary inputs and storage,
interrupted/malformed steps, parallel tools, cross-boundary results, HTTP proxy
forwarding, restart reuse, and a changed suffix invalidating a cached boundary.
The live audit independently checks exact kept bytes and retained reasoning;
its regression deliberately corrupts/removes retained reasoning and detects both.
Intentional removal is counted separately from retained-state preservation.

The realistic live rerun **passed** with `gpt-6.1-sol`, medium reasoning, native
coding tools and native memory MCP. All **29 upstream exchanges returned HTTP
200** and all public inputs/outputs were captured. There were **five distinct
compaction boundaries**, 26 rewritten requests, and no `protected_history`
failures. Requests contained up to six encrypted reasoning items; up to six old
items were removed with their completed steps. Every audit confirmed exact kept
history bytes and retained reasoning. One compaction reduced the request from
**147,169 to 36,835 bytes** (about 75%; byte savings, not token or cost savings).

All 96 audit receipts were absent from both the forwarded request and every
generated summary before the recovery challenge. The probe deleted the source
file, restarted the actual proxy process on the same port, and verified its 62
existing nodes were unchanged. Codex recovered `RECEIPT_032` and `RECEIPT_064`
exactly through original-source `memory_zoom` results, and correctly recalled the
new higher-index tie rule and why it changed. Prior nodes stayed intact; the
disposable project's eight tests and independent behavior assertions passed.

The six turns took 11.27, 49.89, 57.53, 52.35, 47.56 and 44.96 seconds. Rewrite work
totaled **117.83 seconds**, including 20 summary calls: 17 generated model nodes
and three used literal fallback after failure. This demonstrates bounded fallback,
not uniformly successful or low-latency summaries. Final main-session cumulative
usage was 327,895 input tokens (231,680 cached), 5,741 output tokens, and 299
reasoning output tokens (part of output). Summary subprocess usage is additional
and is not included in those totals; no cost comparison is established.

Evidence: `~/.local/share/memory-tool/evals/codex-session/20261007-150752/report.json`,
`proxy-audit.jsonl`, `final-tests.txt`, and the public SQLite archive. The proxy,
Codex session and temporary workspace exited cleanly. Normal client settings and
installed runtimes are unchanged. This closes the earlier reasoning blocker for
the tested Codex format; Claude, unusual provider formats, long-session quality,
and summary latency still need separate validation. The historical failure below
records the behavior before this fix.

## Realistic reasoning-enabled Codex session: 2026-10-07 (failed compaction)

The follow-up workload **did not pass**. It used `gpt-6.1-sol` at medium effort,
native shell/file tools in a disposable project, the real memory MCP registration,
and a separate proxy process configured for model-written summary trees. Codex
diagnosed a faulty integer allocator, implemented it, checked 4,704 deterministic
cases, then changed the tie policy and updated code/tests/docs. The final native
tool output reported eight unit tests and 4,704 property cases passing.

All **14 upstream exchanges returned HTTP 200**, and all public inputs/outputs
were captured. Requests explicitly specified medium reasoning; provider-reported
cumulative usage included **218 reasoning output tokens**, and requests contained
up to five encrypted reasoning items. Those items remained unchanged. Unlike the
earlier simple probe, this exercised the protected-history barrier: **11 requests
reported `protected_history`, zero requests were compacted, and zero summary
nodes/model calls were produced**.

The five work turns took 12.79, 36.86, 20.64, 26.81 and 2.85 seconds. Proxy rewrite
checks totaled 0.20 seconds. Last cumulative provider usage was 214,277 input tokens
(191,872 cached) and 3,487 output tokens; these are usage observations, not a price
estimate. No summary-model usage occurred because the guard prevented reduction.

The probe required two archived audit receipts to be absent from the forwarded
history and all generated summaries before testing recall. None qualified, so it
stopped with `No two archived receipts were demonstrably omitted; recall test
cannot proceed`. **Proxy restart and post-compaction recall were not exercised**;
successful recall from the still-complete history would not establish the desired
behavior. The listener, Codex process and disposable workspace were cleaned up.
Normal client configuration and installed runtimes remain unchanged.

Evidence: `~/.local/share/memory-tool/evals/codex-session/20261007-143525/report.json`,
`proxy-audit.jsonl`, and the public SQLite archive. The new opt-in
`scripts/live_codex_session.py` retains failure diagnostics and returns a nonzero
status when its assertions fail. Ruff and `git diff --check` pass; this turn changed
the probe/documentation, not the production implementation. The prior 187-test
suite result below remains the latest local regression run.

Next implementation requirement: make compaction useful with interleaved encrypted
reasoning while preserving it and valid tool-call/result relationships. Then rerun
the repeated-compaction, actual proxy-process restart and omitted-detail recovery
checks. The earlier live success below is limited to the simpler public-history
probe and does not establish readiness for normal reasoning-enabled sessions.

## Live Codex proxy and summary tree: 2026-10-07

The complete isolated Codex path passed with `gpt-6.1-sol`: existing ChatGPT login,
loopback Responses proxy, model-written summary-tree injection, and the checkout's
actual memory MCP server. The custom provider used `requires_openai_auth=true`,
`supports_websockets=false` and per-thread settings; upstream was
`https://chatgpt.com/backend-api/codex`. Global settings and installed runtimes were
not changed. Only synthetic evidence was used; no Claude process was launched.

The final native-MCP run discovered all five memory tools. Its first reduced
request went from **70,848 to 31,408 bytes**. Two model-written nodes were created
with no model failures, including a merged node. Later requests reused the
injected prefix. All nine upstream exchanges returned HTTP 200 and captured both
input and completed public output. Codex called search, opened two `tree:<id>`
references through native MCP, and paged the original event twice to verify the
exact random marker. The original-source page containing it was checked in the
public tool audit.

Earlier live probes separately verified pass-through and tool-result compaction
(67,635 to 28,552 bytes). A literal-tree probe verified recovery after the random
marker was absent from a forwarded request. In the model-tree runs the summary
retained that marker: they prove summary injection and original-source traversal,
not recovery of a fact omitted by a model summary. This small synthetic sequence
is not a repeated-merge quality evaluation.

Live testing exposed and fixed three integration gaps: the subscription route
omitted Content-Type on SSE responses; ordinary Codex assistant messages carried
public `phase` labels that the tree rejected; and the gateway's zoom adapter
coerced tree references to integers. Missing media type is now accepted only for
explicit streaming requests, still requiring a completed terminal event. Only
known public phases are accepted; unknown metadata and opaque reasoning remain
protected. The first compaction fixture also needed to explicitly emit its tool
result from Codex's code runner; its initial recall success was not counted as
compaction success.

**187 local tests passed** after these fixes; Ruff and `git diff --check` passed.
The final native-MCP probe used the same code plus the explicit MCP test path.
Run the opt-in probe with:

```bash
uv run --frozen python scripts/live_proxy_codex.py --mode tree \
  --summary-model gpt-6.1-sol --native-mcp
```

Reports and synthetic archives are outside the repository under
`~/.local/share/memory-tool/evals/codex-proxy/`. Final native-MCP evidence is in
`20261007-142454-tree/report.json`; the literal omission/recovery probe is
`20261007-142258-tree/report.json`. Probe listeners/processes were stopped and
temporary workspaces removed. The reports retain earlier failures as well.

Limits: this does not enable the proxy for normal Codex launches. The deliberately
low 5,000-token test threshold remained unmet because protected harness/tool and
recent content alone exceeded it. The tested requests contained no encrypted
reasoning items; handling them remains covered by synthetic tests, with early
opaque history still preventing tree reduction. No new desktop-native hook reset,
Claude live test, long-session endurance or general summary-quality test is claimed.

## Incremental summary tree: 2026-10-07 (local only)

Added persistent summary nodes and per-session views, append/merge updates that
never split an existing node during forward progress, and `memory_zoom` traversal
from `tree:<id>` through children to exact original event IDs. Cache identity
includes the prompt, model and byte limit. Raw events remain unchanged. Short
messages use their own flattened lines; model calls are opt-in, bounded by count
and time, and fall back to cached literal excerpts on failure or invalid output.

Proxy `--summary-tree` replaces a complete archived public span after the initial
task and before recent turns. Opaque reasoning, images, citations, unknown
metadata and incomplete/cross-boundary tool pairs prevent replacement of that
span. Initial task, top-level fields and recent suffix bytes stay exact. The
existing tool-result mode remains the default when only `--compact-at` is set.

All **181 local tests passed** (`uv run --frozen pytest -q`, with loopback permission
for mock HTTP servers). New coverage includes a 256-event multilevel tree with
ordered source recovery, restart/replay reuse, append stability, changed/rewound
history, policy identity, project isolation, bounded zoom, concurrent view-update
conflicts, UTF-8 limits, call/time budgets, invalid model answers and cached
fallbacks. Both provider fixtures verify tree injection, exact untouched bytes,
source mappings, protected history, rechecked tool pairs on prefix reuse, archive
failure, signed requests and token-count pass-through. Mock HTTP checks compare
original/forwarded hashes and verify unchanged responses. An initial fixture
mistake in the signed-request test was corrected before the full passing rerun.

Ruff on `src`, `tests` and `scripts`, formatting of new modules/tests, CLI help and
`git diff --check` passed. The Codex low-effort adapter was tested with a stub
subprocess only. No live model calls, runtime installation or client configuration
changes occurred; the earlier evaluation runner was not modified.

Limits: these tests establish tree/storage/transport behavior, not model summary
quality or repeated-merge accuracy. Live subscription routing and provider
acceptance remain unverified. An early opaque reasoning block can prevent any
tree reduction, and protected content may leave the request above its estimated
threshold. The native hook packet selector still uses the existing excerpt tree.
Next validation: a controlled live summary/repeated-merge evaluation, then a
provider acceptance and source-recovery probe when live testing is resumed.

## HTTP proxy stage 2: 2026-10-07 (local only)

Added opt-in `--compact-at`, `--keep-turns` and `--result-chars`. The rule replaces
older archived tool-result text with head/tail excerpts and original event IDs.
It preserves all other JSON bytes, including recent user turns, tool-call/result
structure, signatures and encrypted reasoning. Prefix reuse persists within the
proxy process until another reduction is needed or the original prefix changes.

All **128 local tests passed** (`uv run --frozen pytest -q`, with loopback permission
for mock HTTP servers). Coverage includes both provider formats, exact untouched
byte ranges, tool-result-only user messages, source recovery, changed/reused
prefixes, multi-part text/image results, malformed/duplicate-key JSON, no-reduction
cases, target-unmet reporting, signed/encoded requests and archive-failure fallback.
Local proxy tests verify upstream reduced bodies against original archive contents
and separately recorded original/forwarded hashes; responses remain unchanged.

Ruff on `src`, `tests` and `scripts` and `git diff --check` passed. No live model
calls, quota usage, client configuration changes or runtime installation occurred.
Live subscription routing, provider/signature acceptance and answer quality are
still unverified. Counting uses a local estimate; preserved content may exceed the
threshold. Count-token endpoints remain unchanged. This is a tool-result reduction
rule, not yet the incremental model-written memory tree.

The stage-1 and Claude sections below retain their earlier check counts.

## HTTP proxy stage 1: 2026-10-07 (local mock servers only)

Added `memory-tool proxy`: loopback HTTP pass-through for Anthropic Messages and
OpenAI Responses, with bounded public-evidence capture independent of forwarding.
Request/response bodies stay byte-identical; HTTP framing is rebuilt. No prompt
compaction, client configuration or credential discovery is implemented yet.

Local mock-server tests exercise early streaming delivery, byte/hash equality,
auth and beta-header forwarding without archival leakage, thinking/signature and
encrypted-reasoning exclusion, tool reconstruction, nonstream Responses, upstream
HTTP errors/redirects without retries, archive failure, partial streams, malformed
JSON, bounded/encoded capture, source replay, and loopback/request restrictions.
The first sandbox run could not open sockets (`PermissionError: [Errno 1] Operation
not permitted`); socket tests run with loopback permission. No provider requests,
Claude processes, subscription usage or live configuration changes are involved.

The complete local suite passed: **102 tests** (`uv run --frozen pytest -q`).
Ruff passed on `src`, `tests` and `scripts`; `git diff --check` and the proxy CLI
help check passed. The existing summary-model evaluation runner remains untouched.

Current limits: compressed response bytes are forwarded but not parsed for
capture; unknown blocks are omitted; capture stores public content rather than
raw HTTP. WebSockets and chunked request uploads are unsupported. Subscription
routing and live provider compatibility remain unverified. Rule-based compaction
and incremental summary-tree injection are the next stages.

## Claude native support: 2026-10-07 (offline only)

Added native Claude JSONL capture and `hook --backend claude`, plus an additive
Claude hook/MCP installer with dry-run and backup support. The existing Codex hook
command remains the default. Claude sessions are recognized by memory selection.

Synthetic fixtures cover public text/tool capture, hidden-thinking exclusion,
timestamps and UUID replay, partial lines, bounded capture, transcript rewrites,
generated-summary exclusion, working-directory changes, session isolation,
search/zoom, hook CLI JSON, invalid identities and empty startup transcripts.
Installer fixtures cover separate settings/MCP files, retention of existing hooks
and settings, idempotence, shell quoting, permissions, backups and conflicts.

All 77 local tests passed (`uv run --frozen pytest -q`), Ruff passed on `src`,
`tests` and `scripts`, and `git diff --check` passed. The installer dry-run against
the existing local Claude settings found no conflicts and wrote no files. The
pre-existing uncommitted summary-model evaluation runner was not modified.

No Claude process, model call or subscription test was run. The installed runtime
and live Claude settings were not changed. Supermemory injection remains untouched.
Live hook execution, post-compaction model receipt and fork behavior remain pending
until the user's Claude usage window resets. A prepared packet is not proof that
Claude received it. The proxy and model-written memory tree remain separate work.

The dated sections below retain earlier validation results.

## Version 0.3 follow-up

48 automated tests and Ruff checks passed. Added coverage includes dated status
expiry versus durable preferences, exact original-note zoom and project isolation,
explicit correction sources and unresolved conflicts, same-time/cross-session
claims, question-versus-assertion handling, native session selection, changing
topic vocabulary, whole-document duplicate grouping without merging changed
versions/indentation/backslashes, derived-index migration while an older process
continues writing, bounded deterministic 12k/24k/64k packets, and guarded runtime
bridging. Raw records remain immutable.

The default restoration ceiling is now 24k with an 8k recent allowance; 64k remains
available. The native 160k automatic-compaction / 200k context targets are unchanged.
See [the fixed evaluation report](evaluations/RESULTS-20261004.md) for all raw trial
results, exact-match grading limitations and the failed intermediate iteration.
These are developer-selected regressions, not an independent general quality eval.

The final installed runtime passed actual native compaction with an unseen marker
written only to the external archive after the model's first turn. The model
recovered it through the normally trusted lifecycle hook, without manual context
injection. A fresh native MCP connection discovered all four tools, called status
and search, and recovered the historical spending-cap message with its source ID.
The active desktop conversation also visibly received a policy-3 memory packet
scoped to its own session during normal native compaction. Prepared status alone
is still not proof of receipt, and the latest project status can describe another
chat's recovery operation.

Deployment uses a separate versioned 0.3.0 runtime. The existing approved launcher's
Python entry module dispatches new invocations there; its original module is backed
up. Hook definitions, Codex configuration and approval records were not changed.
Existing MCP processes retain their loaded version until reconnect. Newly launched
hooks and MCP connections use the upgrade without replacing active executables.

The sections below retain earlier-version observations. They do not imply every
older live probe was rerun for 0.3. Claude remains deferred.

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
