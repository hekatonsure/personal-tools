import argparse, asyncio, os, statistics, sys, time
from pathlib import Path
import orjson
from . import embed
from .embed import PASSAGE
from .jev import USD_PER_TOKEN
from .search import equivalent, search, terms
from .sessions import connect, update_index

DB_PATH = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "session-search/index.db"
EVAL_PATH = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "session-search/eval.json"
SHOW_TURN_CHARS = 8000


def _snippet(window: str, offset: int, words: list[str], width: int) -> str:
    """Excerpt around the first query term inside the best-matching passage, else the passage start."""
    body_at = window.find(": ", window.find("\nASSISTANT")) + 2 if "\nASSISTANT" in window else 0
    lo = max(offset, body_at)
    hits = [i for w in words if (i := window.lower().find(w, lo, lo + PASSAGE)) >= 0]
    at = min(hits) - width // 3 if hits else lo
    start = max(body_at, at)
    text = " ".join(window[start:start + width * 2].split())[:width]
    return ("…" if start > body_at else "") + text + ("…" if start + width < len(window) else "")


def _compact(results: list[dict], words: list[str], excerpt_chars: int, full: bool) -> list[dict]:
    if full: return results
    keep = ("id", "source", "cwd", "title", "ended", "p", "resume")
    return [{k: s[k] for k in keep} | {"hits": [
        {"turn": h["turn"], "ts": h["ts"], "p": round(h["p"], 3), "user": " ".join(h["user"].split())[:200],
         "excerpt": _snippet(h["window"], h["offset"], words, excerpt_chars)} for h in s["hits"]]} for s in results]


def _fit(out: list[dict], max_tokens: int | None) -> list[dict]:
    """Drop lowest-ranked sessions until the JSON fits ~max_tokens (4 chars/token); always keep the best one."""
    while max_tokens and len(out) > 1 and len(orjson.dumps(out)) / 4 > max_tokens: out = out[:-1]
    return out


def render(results: list[dict], words: list[str], width: int) -> None:
    home = str(Path.home())
    for rank, s in enumerate(results, 1):
        print(f"\n{rank}. [{s['p']:.2f}] {s['source']} {s['ended'][:10]} {s['cwd'].replace(home, '~', 1)}  {s['title'] or ''}".rstrip())
        print(f"   {s['resume']}")
        for h in s["hits"]:
            user = " ".join(h["user"].split())
            print(f"   ├ turn {h['turn']} [{h['p']:.2f}] » {user[:width - 20]}{'…' if len(user) > width - 20 else ''}")
            print(f"   │   {_snippet(h['window'], h['offset'], words, width)}")


def _index(db, verbose: bool) -> None:
    t = time.monotonic()
    changed, total = update_index(db)
    embedded = embed.sync(db)
    if verbose: print(f"index: {total} sessions, {changed} reparsed, {embedded} embedded ({time.monotonic() - t:.2f}s) → {DB_PATH}", file=sys.stderr)


def show(argv: list[str]) -> None:
    ap = argparse.ArgumentParser(prog="session-search show", description="Print a session's prompt outline, or turns in full.")
    ap.add_argument("id", help="session id or unique prefix")
    ap.add_argument("turn", nargs="?", type=int, help="turn to print; omit for an outline of prompts")
    ap.add_argument("-C", "--context", type=int, default=0, help="neighbouring turns to include")
    ap.add_argument("--full", action="store_true", help=f"don't cap turns at {SHOW_TURN_CHARS} chars")
    a = ap.parse_args(argv)
    db = connect(DB_PATH)
    update_index(db)
    rows = db.execute("select id, source, cwd, title, started, ended, n_turns from sessions where id like ?", (a.id + "%",)).fetchall()
    assert len(rows) == 1, f"{len(rows)} sessions match id prefix {a.id!r}"
    sid, source, cwd, title, started, ended, n = rows[0]
    from .search import resume_cmd
    print(f"{title or '(untitled)'} · {source} · {cwd} · {started[:16]} → {ended[:16]} · {n} turns\n{resume_cmd(source, sid, cwd)}")
    if a.turn is None:
        for idx, ts, text in db.execute("select idx, ts, text from turns where session_id = ? order by idx", (sid,)):
            user = " ".join(text.partition("\nASSISTANT: ")[0].removeprefix("USER: ").split())
            print(f"{idx:4} {ts[:16]}  » {user[:150]}{'…' if len(user) > 150 else ''}")
        return
    for idx, ts, text in db.execute("select idx, ts, text from turns where session_id = ? and idx between ? and ? order by idx",
                                    (sid, a.turn - a.context, a.turn + a.context)):
        if not a.full and len(text) > SHOW_TURN_CHARS:
            text = f"{text[: SHOW_TURN_CHARS * 2 // 3]}\n[… {len(text) - SHOW_TURN_CHARS} chars, --full to see …]\n{text[-SHOW_TURN_CHARS // 3:]}"
        print(f"\n──── turn {idx} · {ts[:16]} {'◀' if idx == a.turn else ''}\n{text}")


def evaluate(argv: list[str]) -> None:
    ap = argparse.ArgumentParser(prog="session-search eval", description=f"Known-answer evaluation; cases from {EVAL_PATH}.")
    ap.add_argument("--cases", type=Path, default=EVAL_PATH, help='{"exclude": [id prefixes], "cases": [{"q": query, "ok": [id prefixes]}]}')
    ap.add_argument("--exhaustive", action="store_true")
    ap.add_argument("--cache", action="store_true", help="allow cached Jev answers (cost/latency then understate)")
    _search_args(ap)
    a = ap.parse_args(argv)
    spec = orjson.loads(a.cases.read_bytes())
    cases, a.exclude = spec["cases"], (a.exclude or []) + spec.get("exclude", [])
    db = connect(DB_PATH)
    _index(db, a.verbose)
    embed.model()  # load once, outside the timed region, as a warm process would
    rows = []
    for c in cases:
        a.query, t = c["q"], time.monotonic()
        results, jev = asyncio.run(search(db, a, cache=a.cache, record=False))
        dt = time.monotonic() - t
        rank = next((i for i, s in enumerate(results, 1) if any(s["id"].startswith(o) for o in c["ok"])), None)
        rows.append((rank, jev.usage.tokens, jev.usage.requests, dt))
        print(f"{rank or '-':>3}  {jev.usage.tokens:>8,} tok {jev.usage.requests:>4} req {dt:5.2f}s  {c['q'][:70]}", flush=True)
    ranks = [r for r, *_ in rows]
    print(f"\n{'exhaustive' if a.exhaustive else 'hybrid'}: hit@1 {sum(r == 1 for r in ranks)}/{len(rows)}  hit@3 {sum(bool(r) and r <= 3 for r in ranks)}/{len(rows)}"
          f"  MRR {statistics.mean(1 / r if r else 0 for r in ranks):.3f}  median {statistics.median(t for *_, t in rows):.2f}s"
          f"  mean {statistics.mean(k for _, k, *_ in rows):,.0f} tok (${statistics.mean(k for _, k, *_ in rows) * USD_PER_TOKEN:.4f})/query")


def _search_args(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--source", choices=["claude", "codex"])
    ap.add_argument("--since", help="e.g. 7d, 3w, 2026-09-01")
    ap.add_argument("--cwd", help="substring of the session's working directory")
    ap.add_argument("--exclude", action="append", metavar="ID", help="skip a session (id prefix); repeatable")
    ap.add_argument("--cards", type=int, default=40, help="session cards Jev reads, picked by local ranking (default 40)")
    ap.add_argument("--depth", type=int, default=12, help="sessions read turn-by-turn after the card pass (default 12)")
    ap.add_argument("--windows", type=int, default=48, help="turn windows Jev reads, picked by local ranking (default 48)")
    ap.add_argument("--excerpts", type=int, default=2, help="excerpts per session (default 2)")
    ap.add_argument("--concurrency", type=int, default=16)
    ap.add_argument("-v", "--verbose", action="store_true")


def main() -> None:
    if sys.argv[1:2] == ["show"]: return show(sys.argv[2:])
    if sys.argv[1:2] == ["eval"]: return evaluate(sys.argv[2:])
    ap = argparse.ArgumentParser(prog="session-search", description="Search Claude Code + Codex sessions with Jev.",
                                 epilog="subcommands: `show <id> [turn] [-C n]` prints a session; `eval` runs the known-answer evaluation")
    ap.add_argument("query", nargs="?", help="plain-English question; omit to just update the index")
    ap.add_argument("-n", type=int, default=8, help="sessions to show (default 8)")
    _search_args(ap)
    ap.add_argument("--exhaustive", action="store_true", help="v1 pipeline: Jev reads every session card (slower, ~7x tokens)")
    ap.add_argument("--json", action="store_true", help="compact machine-readable output")
    ap.add_argument("--full", action="store_true", help="with --json: include whole turn windows instead of excerpts")
    ap.add_argument("--excerpt-chars", type=int, default=500)
    ap.add_argument("--max-tokens", type=int, help="with --json: drop lowest-ranked sessions until output fits")
    ap.add_argument("--fresh", action="store_true", help="always run a new search, never reuse an equivalent recent one")
    args = ap.parse_args()

    db = connect(DB_PATH)
    _index(db, args.verbose or not args.query)
    if not args.query: return
    reused = None if args.fresh else asyncio.run(equivalent(db, args))
    if reused:
        q, p, ts, results = reused
        print(f"reusing “{q}” from {(time.time() - ts) / 3600:.1f}h ago (match p={p:.2f}); --fresh to rerun", file=sys.stderr)
    else:
        results, jev = asyncio.run(search(db, args))
        print(jev.usage, file=sys.stderr)
    results, words = results[: args.n], terms(args.query)
    if args.json: sys.stdout.write(orjson.dumps(_fit(_compact(results, words, args.excerpt_chars, args.full), args.max_tokens), option=orjson.OPT_INDENT_2).decode() + "\n")
    else: render(results, words, width=int(os.environ.get("COLUMNS", 160)) - 30)
