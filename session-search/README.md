# session-search

Plain-English search over local Claude Code and Codex sessions, ranked by [Jev](https://docs.typesafe.ai) (TypeSafe's System One model). Structure borrowed from [jevgrep](https://github.com/dzhng/jevgrep): walk a hierarchy, ask Jev one typed question per item, spell out the criteria in the request.

```sh
ss "where did we decide the wezterm tab colors"     # `ss` alias in ~/.bash_aliases (`command ss` = iproute2)
session-search --since 3w --source codex "kernel 7 MOK signing"
session-search --json "why we switched the cache to sqlite" | jq '.[0].resume'
session-search                 # update the index only (no network)
session-search --dry-run "x"   # cost estimate, no network
```

Each hit prints a ready-to-run `claude --resume <id>` / `codex resume <id>`.

## How it works

1. **Index (offline, incremental).** Parses top-level `~/.claude/projects/*/*.jsonl` and `~/.codex/sessions/**/*.jsonl` into turns of user prompt + assistant text. Tool calls, tool output, thinking, subagent transcripts and Codex guardian/approval-review threads are dropped. Stored in SQLite + FTS5 at `~/.cache/session-search/index.db`. Re-parses only files whose mtime/size changed (~8 s cold, sub-second warm).
2. **Stage 1, session cards.** A card per session (title, cwd, the user's prompts, final reply) gets a Jev `noul`: "likely to contain substantive discussion of the query?" Top `--depth` sessions (default 16) go on. Up to `--depth/2` more come from FTS5 BM25 hits, which catch topics that only appear mid-conversation.
3. **Stage 2, turns.** Every turn of the shortlisted sessions goes to Jev as a 4-level `score`: unrelated → passing mention → discussion → the answer itself. Turns over 6k chars are split into 4.5k windows, and the best window wins. Sessions rank by their best turn.

**Equivalent-query reuse.** Before searching, the 20 most recent searches from the last 7 days with the same filters are offered to Jev as one `choice`, with `none` as an option. A match at p ≥ 0.8 returns that search's stored results in ~0.3 s for about 500 tokens. An exact repeat (ignoring case and whitespace) is matched locally with no Jev call. `--fresh` always runs a new search. Tested: rewordings matched at 0.86–0.98, and same-area different-subject queries were rejected (a calibration question vs an odometry search in the same project, "wezterm render lag" vs the tab-colors search). The closest call was "sign dkms modules for the wifi driver" vs the kernel-7/MOK search at 0.76, just under the threshold.

A typical query costs about 140 Jev requests and ~550k input tokens: **≈ $0.02 and ≈ 2 s**. Answers are cached in the index DB by request hash, so repeating a query is free.

## Batch size matters

Several items share one request (one question per item, `state.items[i]`). With 40 items / 72k chars per request, scores shifted onto neighbouring items. The turn that stated the answer scored 0.27 while the reply after it, which only referred back to it, scored 0.90. Alone, or in small batches, the same two turns score 0.87 / 0.17. TypeSafe's jaggedness notes say the same: accuracy falls as unrelated content grows. Defaults are 16k chars / 8 items (`SS_BATCH_CHARS`, `SS_BATCH_ITEMS`). On 3 known-answer queries this matched 8k/4 quality with half the requests.

## Privacy

Session text goes to `api.typesafe.ai`. Before it is sent, `jev.redact` strips private keys, `sk-`/`ghp_`/`xox*`/`AKIA`/`AIza`/`jv_live_` tokens, JWTs, bearer tokens and `password=`/`token:`-style values. This is a best-effort regex, not a guarantee. Use `--cwd` / `--source` / `--since` to limit what gets judged.

## Setup

Needs `TYPESAFE_API_KEY` in the environment (create a key at console.typesafe.ai).

```sh
uv tool install -e ~/personal-tools/session-search   # puts session-search on PATH; edits apply live
uv run pytest -q                                      # offline tests, synthetic fixtures only
```

The agent skill is in `skill/SKILL.md`. It is linked into `~/.agents/skills/session-search` (Codex) and `~/.claude/skills/session-search` (Claude Code).
