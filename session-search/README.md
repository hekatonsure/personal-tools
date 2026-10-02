# session-search

Plain-English search over local Claude Code and Codex sessions. A free local ranker picks what [Jev](https://docs.typesafe.ai) (TypeSafe's System One model) reads, and Jev makes the final judgment. Structure borrowed from [jevgrep](https://github.com/dzhng/jevgrep) (ask Jev one typed question per item, spell out the criteria). Local-first retrieval borrowed from [cass](https://github.com/Dicklesworthstone/coding_agent_session_search) (BM25 + static embeddings fused with RRF, compact agent output, expand-a-hit).

```sh
ss "where did we decide the wezterm tab colors"     # `ss` alias in ~/.bash_aliases (`command ss` = iproute2)
session-search --since 3w --source codex "kernel 7 MOK signing"
session-search --json --max-tokens 1500 "why we switched the cache to sqlite"   # compact, budgeted, for agents
session-search show 0d8ee47a          # outline: every prompt with its turn number
session-search show 0d8ee47a 14 -C 1  # turn 14 plus one neighbour each side, as clean text
session-search                        # update the index only (no network)
```

Each hit prints a ready-to-run `claude --resume <id>` / `codex resume <id>`.

## How it works

1. **Index (offline, incremental).** Parses top-level `~/.claude/projects/*/*.jsonl` and `~/.codex/sessions/**/*.jsonl` into turns of user prompt + assistant text. Tool calls, tool output, thinking, subagent transcripts and Codex guardian/approval-review threads are dropped. Every turn window is split into 1.2k-char passages and embedded locally with [model2vec](https://github.com/MinishLab/model2vec) `potion-retrieval-32M` (static embeddings, numpy only, pinned revision). Everything is stored in SQLite + FTS5 at `~/.cache/session-search/index.db`. Only changed files are re-parsed and re-embedded: the full corpus (6.6k windows) embeds in about 2 s, and a warm update takes about 0.15 s.
2. **Local ranking (free, ~30 ms).** Sessions are ranked by reciprocal-rank fusion of three lists: card embedding similarity, best-passage similarity, and BM25 over turns. The top `--cards` (40) go on.
3. **Jev on cards.** Each card (title, cwd, prompts, final reply) gets a `noul`: "likely to contain substantive discussion of the query?" The top `--depth` (12) sessions go on, plus up to 6 local-ranked sessions, since cards miss topics that only come up mid-conversation.
4. **Jev on turns.** Within those sessions, windows are ranked locally (embedding RRF BM25), and the top `--windows` (48, with at least 2 per session) get a 4-level `score`: unrelated → passing mention → discussion → the answer itself. Sessions rank by their best turn. Excerpts are centred on the best-matching passage.

`--exhaustive` runs the v1 pipeline, in which Jev reads every card and every window of the shortlist.

### Hybrid vs exhaustive

On 14 known-answer queries from my own history (kept outside the repo), with Jev's cache disabled:

| | hit@1 | hit@3 | median latency | tokens / query |
|---|---|---|---|---|
| exhaustive (v1) | 14/14 | 14/14 | 1.27 s | 448k ($0.019) |
| **hybrid** | **14/14** | 14/14 | **0.38 s** | **66k ($0.003)** |

The first hybrid run scored 13/14. The miss turned out to be a missing label: the top hit was the turn where the decision was originally made. Run your own with `session-search eval` and cases in `~/.config/session-search/eval.json`: `{"exclude": [...], "cases": [{"q": ..., "ok": [id prefixes]}]}`.

End to end, including process start, index refresh and model load, a fresh search takes about 0.9 s.

### Equivalent-query reuse

An exact repeat (ignoring case and whitespace) is matched locally. Otherwise, recent searches (last 7 days, same filters) are first filtered by embedding similarity ≥ 0.30, and only survivors are offered to Jev as one `choice` with a `none` option. A match at p ≥ 0.8 returns the stored results (~0.5 s). Embeddings alone can't make this call: a true rewording scored 0.46 while a same-area, different question scored 0.41. As a gate, they skip the Jev round trip for unrelated queries. `--fresh` always searches.

## Output for agents

`--json` is compact by default: per session `id, source, cwd, title, ended, p, resume`; per hit `turn, ts, p, user (≤200 chars), excerpt (--excerpt-chars, 500)`. That's about 10 KB for 8 sessions, against 42 KB with `--full` windows. `--max-tokens N` drops the lowest-ranked sessions until the JSON fits. Follow a hit with `show <id> <turn>` instead of opening multi-MB JSONL.

## Batch size matters

Several items share one request (one question per item, `state.items[i]`). With 40 items / 72k chars per request, scores shifted onto neighbouring items. The turn that stated the answer scored 0.27, while the reply after it, which only referred back to it, scored 0.90. Alone or in small batches, the same two turns score 0.87 / 0.17. TypeSafe's jaggedness notes say the same: accuracy falls as unrelated content grows. Defaults are 16k chars / 8 items (`SS_BATCH_CHARS`, `SS_BATCH_ITEMS`).

## Privacy

Session text goes to `api.typesafe.ai`, but hybrid mode sends only ~40 cards and ~48 windows per query, not every card. Before sending, `jev.redact` strips private keys, `sk-`/`ghp_`/`xox*`/`AKIA`/`AIza`/`jv_live_` tokens, JWTs, bearer tokens and `password=`/`token:`-style values. This is a best-effort regex, not a guarantee. Embedding and BM25 run locally. Use `--cwd` / `--source` / `--since` / `--exclude` to limit scope.

## Setup

Needs `TYPESAFE_API_KEY` in the environment (create a key at console.typesafe.ai). On first run, the embedding model (~130 MB) downloads from Hugging Face at a pinned revision. After that it loads from the local cache with no network calls.

```sh
uv tool install -e ~/personal-tools/session-search   # puts session-search on PATH; re-run with --reinstall after dependency changes
uv run pytest -q                                      # offline tests, synthetic fixtures only
```

The agent skill is in `skill/SKILL.md`. It is linked into `~/.agents/skills/session-search` (Codex) and `~/.claude/skills/session-search` (Claude Code).
