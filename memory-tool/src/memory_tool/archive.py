from __future__ import annotations
import hashlib, json, os, sqlite3, time, uuid
from pathlib import Path


def canonical(project: str) -> str:
    value = os.path.normpath(os.path.abspath(project))
    return value.lower() if os.name == "nt" else value


def data_dir() -> Path:
    return Path(
        os.environ.get("MEMORY_TOOL_HOME", Path.home() / ".local/share/memory-tool")
    )


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class Archive:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or data_dir() / "memory.sqlite")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS chats(id TEXT PRIMARY KEY,project TEXT NOT NULL,created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,chat TEXT NOT NULL,source TEXT NOT NULL,ts TEXT NOT NULL,role TEXT NOT NULL,text TEXT NOT NULL,raw TEXT NOT NULL,UNIQUE(chat,source));
        CREATE TRIGGER IF NOT EXISTS event_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT,'immutable event');END;
        CREATE TRIGGER IF NOT EXISTS event_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT,'immutable event');END;
        CREATE TABLE IF NOT EXISTS provenance(chat TEXT NOT NULL,source TEXT NOT NULL,event INTEGER NOT NULL,raw TEXT NOT NULL,UNIQUE(chat,source));
        CREATE TABLE IF NOT EXISTS notes(chat TEXT NOT NULL,id TEXT NOT NULL,record TEXT NOT NULL,UNIQUE(chat,id));
        CREATE TABLE IF NOT EXISTS nodes(chat TEXT NOT NULL,lo INTEGER NOT NULL,hi INTEGER NOT NULL,excerpts TEXT NOT NULL,UNIQUE(chat,lo,hi));
        CREATE VIRTUAL TABLE IF NOT EXISTS passages USING fts5(text,chat UNINDEXED,event UNINDEXED,start UNINDEXED);
        CREATE TABLE IF NOT EXISTS checkpoints(id TEXT PRIMARY KEY,chat TEXT NOT NULL,created REAL NOT NULL,watermark INTEGER NOT NULL,sha TEXT NOT NULL,packet TEXT NOT NULL,tokens INTEGER NOT NULL,encoding TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY,chat TEXT NOT NULL,thread TEXT,state TEXT NOT NULL,detail TEXT NOT NULL,updated REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS ranking_cache(key TEXT PRIMARY KEY,value TEXT NOT NULL,created REAL NOT NULL);
        """)

    def close(self):
        self.db.close()

    def register(self, chat: str, project: str):
        row = self.db.execute(
            "SELECT project FROM chats WHERE id=?", (chat,)
        ).fetchone()
        project = canonical(project)
        if row and row[0] != project:
            raise ValueError("Chat project cannot change")
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO chats VALUES(?,?,?)",
                (chat, project, time.time()),
            )

    def project(self, chat: str) -> str:
        row = self.db.execute(
            "SELECT project FROM chats WHERE id=?", (chat,)
        ).fetchone()
        if not row:
            raise ValueError("Unknown chat")
        return row[0]

    def append(
        self,
        chat: str,
        role: str,
        text: str,
        source: str | None = None,
        raw: str = "",
        ts: str | None = None,
    ) -> int:
        self.project(chat)
        source = source or uuid.uuid4().hex
        ts = ts or str(time.time_ns())
        with self.db:
            old = self.db.execute(
                "SELECT id,role,text FROM events WHERE chat=? AND source=?",
                (chat, source),
            ).fetchone()
            if old:
                if old["role"] != role or old["text"] != text:
                    raise ValueError("Source identity collision")
                return old["id"]
            row = self.db.execute(
                "INSERT INTO events(chat,source,ts,role,text,raw) VALUES(?,?,?,?,?,?)",
                (chat, source, ts, role, text, raw),
            )
            event = row.lastrowid
            for start in [] if role == "generated_memory" else range(0, len(text), 768):
                self.db.execute(
                    "INSERT INTO passages VALUES(?,?,?,?)",
                    (text[start : start + 1024], chat, event, start),
                )
                if start + 1024 >= len(text):
                    break
        return event

    def events(self, chat: str):
        return [
            dict(r)
            for r in self.db.execute(
                "SELECT * FROM events WHERE chat=? AND role!='generated_memory' ORDER BY id",
                (chat,),
            )
        ]

    def event_index(self, chat: str):
        return [
            dict(r)
            for r in self.db.execute(
                "SELECT id,role,ts,substr(text,1,480) AS preview FROM events WHERE chat=? AND role!='generated_memory' ORDER BY id",
                (chat,),
            )
        ]

    def event(self, chat: str, event: int):
        row = self.db.execute(
            "SELECT * FROM events WHERE chat=? AND id=?", (chat, event)
        ).fetchone()
        if not row:
            raise ValueError("Event does not belong to chat")
        return dict(row)

    def zoom(
        self,
        chat: str,
        event: int,
        offset: int = 0,
        budget: int = 2000,
        encoding: str = "o200k_base",
    ):
        from .packing import Tokens

        if not 128 <= budget <= 8000:
            raise ValueError("Zoom budget must be 128..8000 tokens")
        row = self.db.execute(
            "SELECT * FROM events WHERE chat=? AND id=?", (chat, event)
        ).fetchone()
        if not row:
            raise ValueError("Event does not belong to chat")
        if offset < 0 or offset > len(row["text"]):
            raise ValueError("Invalid offset")
        counter = Tokens(encoding)
        base = {
            "chat": chat,
            "event": event,
            "role": row["role"],
            "start": offset,
            "text": "",
            "next_offset": None,
        }
        text = row["text"][offset:]
        lo = 0
        hi = len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            candidate = {
                **base,
                "text": text[:mid],
                "next_offset": offset + mid if mid < len(text) else None,
            }
            if counter.count(json.dumps(candidate, ensure_ascii=False)) <= budget:
                lo = mid
            else:
                hi = mid - 1
        if not lo and text:
            raise ValueError("Zoom budget too small")
        return {
            **base,
            "text": text[:lo],
            "next_offset": offset + lo if lo < len(text) else None,
        }

    def search(self, chat: str, query: str, limit: int = 24):
        import re

        self.project(chat)
        if not query.strip() or len(query) > 2000:
            raise ValueError("Search query must contain 1..2000 characters")
        if not 1 <= limit <= 24:
            raise ValueError("Search limit must be 1..24")
        words = list(dict.fromkeys(re.findall(r"\w+", query, flags=re.UNICODE)))[:16]
        if not words:
            return []
        match = " OR ".join('"' + w.replace('"', '""') + '"' for w in words)
        return [
            dict(r)
            for r in self.db.execute(
                "SELECT p.*,e.role,e.ts,bm25(passages) AS rank FROM passages p JOIN events e ON e.id=p.event WHERE passages MATCH ? AND p.chat=? ORDER BY rank LIMIT ?",
                (match, chat, limit),
            )
        ]

    def import_connectome(
        self, chat: str, path: str | Path, project: str, session: str | None = None
    ):
        self.register(chat, project)
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(path)
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        before = len(self.events(chat))
        provenance = 0
        try:
            connection.execute("BEGIN")
            sql = "SELECT e.* FROM events e JOIN sessions s ON s.id=e.session WHERE s.project=?"
            params = [canonical(project)]
            if session:
                sql += " AND e.session=?"
                params.append(session)
            sql += " ORDER BY e.ts,e.session,e.seq"
            for r in connection.execute(sql, params):
                # Preserve every observed source occurrence separately, collapse replayed semantic events.
                source = f"connectome:{r['session']}:{r['seq']}"
                if self.db.execute(
                    "SELECT 1 FROM provenance WHERE chat=? AND source=?", (chat, source)
                ).fetchone():
                    continue
                key = f"native:{r['session']}:{r['ts']}:{r['role']}:{digest(r['text'])}"
                # Generated memory is retained in provenance, not recursively repacked as new evidence.
                generated = r["role"] == "developer" and (
                    "MEMORY-TOOL/v1" in r["text"]
                    or r["text"].startswith("# Connectome memory")
                )
                role = "generated_memory" if generated else r["role"]
                event = self.append(chat, role, r["text"], key, r["raw"], r["ts"])
                with self.db:
                    self.db.execute(
                        "INSERT OR IGNORE INTO provenance VALUES(?,?,?,?)",
                        (chat, source, event, r["raw"]),
                    )
                provenance += 1
        finally:
            connection.close()
        notes_path = path.parent / "notes.jsonl"
        if notes_path.exists():
            for line in notes_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                record = json.loads(line)
                if (
                    record.get("project")
                    and canonical(record["project"]) == canonical(project)
                    and record.get("id")
                ):
                    with self.db:
                        self.db.execute(
                            "INSERT OR IGNORE INTO notes VALUES(?,?,?)",
                            (chat, record["id"], line),
                        )
        return {
            "new_events": len(self.events(chat)) - before,
            "new_source_occurrences": provenance,
        }

    def active_notes(self, chat: str):
        rows = [
            json.loads(r[0])
            for r in self.db.execute("SELECT record FROM notes WHERE chat=?", (chat,))
        ]
        invalid = {
            n for r in rows for n in r.get("supersedes", []) + r.get("retracts", [])
        }
        return sorted(
            [
                r
                for r in rows
                if r.get("kind", "note") == "note" and r["id"] not in invalid
            ],
            key=lambda r: (r.get("type") == "pin", r.get("ts", "")),
            reverse=True,
        )

    def operation(
        self, id: str, chat: str, thread: str | None, state: str, detail: dict
    ):
        with self.db:
            self.db.execute(
                "INSERT INTO operations VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET state=excluded.state,detail=excluded.detail,updated=excluded.updated",
                (id, chat, thread, state, json.dumps(detail), time.time()),
            )

    def stats(self, chat: str):
        self.project(chat)
        return {
            "chat": chat,
            "project": self.project(chat),
            "events": self.db.execute(
                "SELECT count(*) FROM events WHERE chat=?", (chat,)
            ).fetchone()[0],
            "source_occurrences": self.db.execute(
                "SELECT count(*) FROM provenance WHERE chat=?", (chat,)
            ).fetchone()[0],
            "checkpoints": self.db.execute(
                "SELECT count(*) FROM checkpoints WHERE chat=?", (chat,)
            ).fetchone()[0],
        }
