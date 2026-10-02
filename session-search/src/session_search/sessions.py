"""Parse Claude Code and Codex session logs into turns and keep them in an incremental SQLite index."""
import re, sqlite3
from dataclasses import dataclass, field
from pathlib import Path
import orjson

CLAUDE_ROOT, CODEX_ROOT = Path.home() / ".claude/projects", Path.home() / ".codex/sessions"
CODEX_INDEX = Path.home() / ".codex/session_index.jsonl"
SCHEMA_VERSION = 2
TURN_CHARS, WINDOW, STRIDE, MAX_WINDOWS = 6000, 4500, 4000, 12  # long turns are split into overlapping windows

_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
_CMD = re.compile(r"<command-name>(.*?)</command-name>.*?(?:<command-args>(.*?)</command-args>)?", re.S)
# Harness-injected user messages in Codex rollouts (AGENTS.md, env context, skill lists, ...)
_CODEX_INJECTED = ("# AGENTS.md", "<environment_context", "<user_instructions", "<skills_instructions", "<permissions",
                   "<turn_aborted", "<user_shell_command", "<collaboration_mode", "<subagent")


@dataclass(slots=True)
class Turn:
    ts: str
    user: str
    assistant: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Session:
    id: str
    source: str  # "claude" | "codex"
    path: str
    cwd: str = ""
    title: str = ""
    turns: list[Turn] = field(default_factory=list)


def _clean_user(text: str) -> str | None:
    text = _REMINDER.sub("", text).strip()
    if m := _CMD.search(text): return f"{m[1].strip()} {(m[2] or '').strip()}".strip()
    if not text or text.startswith(("<local-command", "Caveat:", "[Request interrupted")): return None
    return text


def _records(path: Path):
    # crash-truncated logs can contain runs of NUL bytes; anything else malformed should still fail loudly
    for line in path.open("rb"):
        if line := line.strip(b"\x00 \t\r\n"): yield orjson.loads(line)


def _add(s: Session, ts: str, user: str | None = None, assistant: str | None = None) -> None:
    if user: s.turns.append(Turn(ts, user))
    elif assistant and (assistant := assistant.strip()):
        if not s.turns: s.turns.append(Turn(ts, ""))
        s.turns[-1].assistant.append(assistant)


def parse_claude(path: Path) -> Session:
    s = Session(path.stem, "claude", str(path))
    for r in _records(path):
        t = r.get("type")
        if t == "ai-title": s.title = r["aiTitle"]
        if t not in ("user", "assistant") or r.get("isSidechain") or r.get("isMeta") or r.get("isCompactSummary"): continue
        s.cwd = s.cwd or r.get("cwd", "")
        content, ts = r["message"]["content"], r.get("timestamp", "")
        blocks = [content] if isinstance(content, str) else [b["text"] for b in content if b.get("type") == "text"]
        for text in blocks:
            if t == "user": _add(s, ts, user=_clean_user(text))
            else: _add(s, ts, assistant=text)
    return s


def parse_codex(path: Path, titles: dict[str, str]) -> Session:
    s = Session("", "codex", str(path))
    for r in _records(path):
        p, ts = r.get("payload") or {}, r.get("timestamp", "")
        if r["type"] == "session_meta":
            s.id, s.cwd = p["id"], p.get("cwd", "")
            # guardian approval reviews and spawned threads are agent-to-agent; keep only human sessions
            if isinstance(p.get("source"), dict) and "subagent" in p["source"]: return s
        if r["type"] != "response_item" or p.get("type") != "message": continue
        texts = [c["text"] for c in p["content"] if c.get("type") in ("input_text", "output_text")]
        for text in texts:
            if p["role"] == "user" and not text.lstrip().startswith(_CODEX_INJECTED): _add(s, ts, user=_clean_user(text))
            elif p["role"] == "assistant": _add(s, ts, assistant=text)
    assert s.id, f"no session_meta in {path}"
    s.title = titles.get(s.id, "")
    return s


def _codex_titles() -> dict[str, str]:
    if not CODEX_INDEX.exists(): return {}
    rows = [orjson.loads(l) for l in CODEX_INDEX.open("rb")]
    # later rows win; "$skill" placeholder names get replaced by the generated title
    return {r["id"]: r["thread_name"] for r in rows if not r["thread_name"].startswith("$")}


def session_files() -> list[tuple[str, Path]]:
    # top-level only: subagent transcripts live under <project>/<session>/subagents/
    return [("claude", p) for p in CLAUDE_ROOT.glob("*/*.jsonl")] + [("codex", p) for p in CODEX_ROOT.glob("*/*/*/*.jsonl")]


def windows(text: str) -> list[str]:
    """A turn as the units Jev judges: whole if short, else user head + overlapping slices of the reply."""
    if len(text) <= TURN_CHARS: return [text]
    user, sep, reply = text.partition("\nASSISTANT: ")
    head, starts = user[:800], range(0, max(1, len(reply) - WINDOW + STRIDE), STRIDE)
    parts = [reply[i:i + WINDOW] for i in starts][:MAX_WINDOWS]
    return [f"{head}\nASSISTANT (part {k + 1}/{len(parts)}): {w}" for k, w in enumerate(parts)]


def turn_text(user: str, assistant: str) -> str:
    return f"USER: {user}\nASSISTANT: {assistant}" if user else f"ASSISTANT: {assistant}"


def card_text(title: str, cwd: str, prompts: list[str], outcome: str, budget: int = 1600) -> str:
    """Compact session summary for the first Jev pass: title, project, the user's prompts and the final reply."""
    prompts = [p for p in prompts if not re.fullmatch(r"/[\w:-]+", p)]  # bare /clear, /catchup carry no topic
    head = f"title: {title or '(none)'}\nproject: {cwd}\nprompts:"
    per = max(120, budget // max(1, len(prompts)))
    body = "".join(f"\n- {' '.join(p.split())[:per]}" for p in prompts)[:budget]
    return f"{head}{body}\nfinal reply: {' '.join(outcome.split())[:400]}"


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    db = sqlite3.connect(db_path)
    db.executescript(f"""
        pragma journal_mode=wal;
        create table if not exists meta(k text primary key, v text);
        create table if not exists sessions(id text primary key, source text, path text unique, mtime real, size int,
            cwd text, title text, started text, ended text, n_turns int, card text);
        create table if not exists turns(session_id text, idx int, ts text, text text, primary key(session_id, idx));
        create virtual table if not exists turns_fts using fts5(text, session_id unindexed, idx unindexed);
        create table if not exists jev_cache(key text primary key, answers blob);
        create table if not exists searches(ts real, query text, filters text, results blob);
        create table if not exists emb(session_id text primary key, model text, mtime real, keys blob, vecs blob);
    """)
    if db.execute("select v from meta where k='schema'").fetchone() != (str(SCHEMA_VERSION),):
        db.executescript("delete from sessions; delete from turns; delete from turns_fts;")
        db.execute("insert or replace into meta values('schema', ?)", (str(SCHEMA_VERSION),))
    return db


def update_index(db: sqlite3.Connection) -> tuple[int, int]:
    """Reparse new/changed session files. Returns (reparsed, total)."""
    known = {path: (mtime, size) for path, mtime, size in db.execute("select path, mtime, size from sessions")}
    files = session_files()
    titles, changed = _codex_titles(), 0
    for source, path in files:
        st = path.stat()
        if known.get(str(path)) == (st.st_mtime, st.st_size): continue
        s = parse_claude(path) if source == "claude" else parse_codex(path, titles)
        _store(db, s, st.st_mtime, st.st_size)
        changed += 1
    gone = set(known) - {str(p) for _, p in files}
    for path in gone: _delete(db, *db.execute("select id from sessions where path=?", (path,)).fetchone())
    db.commit()
    return changed, len(files)


def _delete(db: sqlite3.Connection, sid: str) -> None:
    for t in ("turns", "turns_fts", "emb"): db.execute(f"delete from {t} where session_id=?", (sid,))
    db.execute("delete from sessions where id=?", (sid,))


def _store(db: sqlite3.Connection, s: Session, mtime: float, size: int) -> None:
    _delete(db, s.id)
    db.execute("delete from sessions where path=?", (s.path,))
    rows = [(s.id, i, t.ts, turn_text(t.user, "\n\n".join(t.assistant))) for i, t in enumerate(s.turns)]
    db.executemany("insert into turns values(?,?,?,?)", rows)
    db.executemany("insert into turns_fts(text, session_id, idx) values(?,?,?)", [(r[3], r[0], r[1]) for r in rows])
    ts = [t.ts for t in s.turns if t.ts]
    card = card_text(s.title, s.cwd, [t.user for t in s.turns if t.user], s.turns[-1].assistant[-1] if s.turns and s.turns[-1].assistant else "")
    db.execute("insert into sessions values(?,?,?,?,?,?,?,?,?,?,?)",
               (s.id, s.source, s.path, mtime, size, s.cwd, s.title, min(ts, default=""), max(ts, default=""), len(s.turns), card))
