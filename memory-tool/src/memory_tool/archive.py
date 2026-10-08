from __future__ import annotations
import hashlib, json, os, sqlite3, time, uuid
from pathlib import Path
from .evidence import CATALOG_VERSION, decision_question, query_words, source_kind
from .selection import LABEL_VERSION, duplicate_key, freshness, image_free, normalized


FEEDBACK = ("useful", "noise", "stale", "wrong", "missing")
# Latest agent verdict for a document; copies of one document share it.
LATEST_FEEDBACK = "(SELECT verdict||':'||note FROM feedback f WHERE f.chat=e.chat AND f.dup=c.dup ORDER BY f.ts DESC LIMIT 1) AS feedback"


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
        CREATE TABLE IF NOT EXISTS summary_nodes(chat TEXT NOT NULL,id TEXT NOT NULL,record TEXT NOT NULL,PRIMARY KEY(chat,id));
        CREATE TABLE IF NOT EXISTS summary_views(chat TEXT NOT NULL,scope TEXT NOT NULL,state TEXT NOT NULL,PRIMARY KEY(chat,scope));
        CREATE VIRTUAL TABLE IF NOT EXISTS passages USING fts5(text,chat UNINDEXED,event UNINDEXED,start UNINDEXED);
        CREATE TABLE IF NOT EXISTS checkpoints(id TEXT PRIMARY KEY,chat TEXT NOT NULL,created REAL NOT NULL,watermark INTEGER NOT NULL,sha TEXT NOT NULL,packet TEXT NOT NULL,tokens INTEGER NOT NULL,encoding TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY,chat TEXT NOT NULL,thread TEXT,state TEXT NOT NULL,detail TEXT NOT NULL,updated REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS ranking_cache(key TEXT PRIMARY KEY,value TEXT NOT NULL,created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS derived_versions(name TEXT PRIMARY KEY,version INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS labels(dup TEXT PRIMARY KEY,version INTEGER NOT NULL,vector TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS feedback(chat TEXT NOT NULL,dup TEXT,event INTEGER,verdict TEXT NOT NULL,note TEXT NOT NULL,ts REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS feedback_dup ON feedback(chat,dup,ts);
        """)
        # Derived metadata can evolve without modifying immutable public events.
        with self.db:
            version = self.db.execute(
                "SELECT version FROM derived_versions WHERE name='catalog'"
            ).fetchone()
            if not version or version[0] < CATALOG_VERSION:
                self.db.execute("DROP TABLE IF EXISTS evidence_catalog")
                self.db.execute(
                    "CREATE TABLE evidence_catalog(event INTEGER PRIMARY KEY,kind TEXT NOT NULL,dup TEXT NOT NULL)"
                )
                self.db.execute(
                    "CREATE INDEX evidence_kind ON evidence_catalog(kind,event)"
                )
                self.db.execute(
                    "INSERT OR REPLACE INTO derived_versions VALUES('catalog',?)",
                    (CATALOG_VERSION,),
                )
            watermark = self.db.execute(
                "SELECT version FROM derived_versions WHERE name='catalog_watermark'"
            ).fetchone()
            missing = self.db.execute(
                "SELECT e.id,e.role,e.ts,e.text FROM events e LEFT JOIN evidence_catalog c ON c.event=e.id WHERE c.event IS NULL OR e.id>?",
                (watermark[0] if watermark else 0,),
            ).fetchall()
            self.db.executemany(
                "INSERT OR REPLACE INTO evidence_catalog VALUES(?,?,?)",
                [
                    (r["id"], source_kind(r["role"], r["text"]), duplicate_key(dict(r)))
                    for r in missing
                ],
            )
            if missing:
                self.db.execute("DELETE FROM nodes")
                self.db.execute(
                    "INSERT OR REPLACE INTO derived_versions VALUES('catalog_watermark',?)",
                    (max(r["id"] for r in missing),),
                )

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

    def chat_for_project(self, project: str):
        project = canonical(project)
        rows = self.db.execute(
            "SELECT id FROM chats WHERE project=? ORDER BY created,id", (project,)
        ).fetchall()
        if len(rows) == 1:
            return rows[0][0]
        # Multiple explicitly created chats remain separate. A deterministic project
        # chat gives native hooks/MCP the same scope without merging their archives.
        chat = "project-" + digest(project)[:16]
        self.register(chat, project)
        return chat

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
            self.db.execute(
                "INSERT INTO evidence_catalog VALUES(?,?,?)",
                (
                    event,
                    source_kind(role, text),
                    duplicate_key({"role": role, "ts": ts, "text": text}),
                ),
            )
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
                "SELECT e.* FROM events e JOIN evidence_catalog c ON c.event=e.id WHERE chat=? AND c.kind NOT IN ('generated','snapshot_summary','transcript_replay','retrieval_echo','scaffolding','review_metadata') ORDER BY e.id",
                (chat,),
            )
        ]

    def event_index(self, chat: str):
        return [
            {
                **r,
                "preview": image_free(r["preview"]),
                "labels": json.loads(r["labels"]) if r["labels"] else None,
            }
            for r in self.db.execute(
                f"SELECT id,role,ts,source,c.kind,c.dup,substr(text,1,480) AS preview,CASE WHEN role='assistant' AND json_valid(raw) THEN coalesce(json_extract(raw,'$.payload.phase'),json_extract(raw,'$.phase'),json_extract(raw,'$.payload.channel'),json_extract(raw,'$.channel')) END AS phase,l.vector AS labels,{LATEST_FEEDBACK} FROM events e JOIN evidence_catalog c ON c.event=e.id LEFT JOIN labels l ON l.dup=c.dup AND l.version=? WHERE chat=? AND c.kind NOT IN ('generated','snapshot_summary','transcript_replay','retrieval_echo','scaffolding','review_metadata') ORDER BY id",
                (LABEL_VERSION, chat),
            )
        ]

    def add_feedback(self, chat: str, verdict: str, note: str = "", event=None):
        """Agent judgment on a memory record. It annotates selection; evidence is unchanged."""
        assert verdict in FEEDBACK, (
            f"verdict must be one of {FEEDBACK}, got {verdict!r}"
        )
        assert len(note) <= 500, "feedback note is limited to 500 characters"
        if verdict == "missing":
            assert note.strip(), "missing feedback must describe what was missing"
            dup = event = None
        else:
            assert event is not None, f"{verdict} feedback needs an event"
            self.event(chat, event)
            dup = self.db.execute(
                "SELECT dup FROM evidence_catalog WHERE event=?", (event,)
            ).fetchone()[0]
        with self.db:
            self.db.execute(
                "INSERT INTO feedback VALUES(?,?,?,?,?,?)",
                (chat, dup, event, verdict, note, time.time()),
            )
        return {
            "recorded": verdict,
            "event": event,
            "applies_to_copies": dup is not None,
        }

    def feedback_summary(self, chat: str):
        return {
            "verdicts": dict(
                self.db.execute(
                    "SELECT verdict,count(*) FROM feedback WHERE chat=? GROUP BY verdict",
                    (chat,),
                ).fetchall()
            ),
            "recent_missing": [
                r[0]
                for r in self.db.execute(
                    "SELECT note FROM feedback WHERE chat=? AND verdict='missing' ORDER BY ts DESC LIMIT 5",
                    (chat,),
                )
            ],
        }

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
        event: int | str,
        offset: int = 0,
        budget: int = 2000,
        encoding: str = "o200k_base",
    ):
        from .packing import Tokens

        if not 128 <= budget <= 8000:
            raise ValueError("Zoom budget must be 128..8000 tokens")
        if isinstance(event, str) and event.startswith("tree:"):
            from .summary_tree import zoom_text

            row = {"role": "derived_summary", "text": zoom_text(self, chat, event[5:])}
        elif isinstance(event, str) and event.startswith("note:"):
            saved = self.db.execute(
                "SELECT record FROM notes WHERE chat=? AND id=?", (chat, event[5:])
            ).fetchone()
            row = {"role": "curated_note", "text": saved[0]} if saved else None
        else:
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
        self.project(chat)
        if not query.strip() or len(query) > 2000:
            raise ValueError("Search query must contain 1..2000 characters")
        if not 1 <= limit <= 24:
            raise ValueError("Search limit must be 1..24")
        words = query_words(query)
        if not words:
            return []
        match = " OR ".join('"' + w.replace('"', '""') + '"' for w in words)
        rows = self.db.execute(
            f"SELECT p.*,e.role,e.ts,e.source,c.kind,c.dup,{LATEST_FEEDBACK},bm25(passages) AS rank FROM passages p JOIN events e ON e.id=p.event JOIN evidence_catalog c ON c.event=e.id WHERE passages MATCH ? AND p.chat=? AND c.kind NOT IN ('generated','snapshot_summary','transcript_replay','retrieval_echo','retrieval_call','scaffolding','review_metadata') ORDER BY (e.role='tool_call'),(? AND e.role NOT IN ('user','assistant')),rank,e.id,p.start",
            (match, chat, decision_question(query)),
        )
        # Dedupe BEFORE paid ranking. Repeated words/overlapping windows from one
        # event must not crowd other source events out of the shortlist.
        selected, used, copies = [], set(), {}
        for row in rows:
            row = dict(row)
            if row["event"] in used:
                continue
            used.add(row["event"])
            # Compare complete documents, not matching windows: two versions can
            # share a paragraph while disagreeing elsewhere.
            key = row["dup"]
            if row["role"] in {"user", "assistant"}:
                # Presentation only: identical dated statements share one slot,
                # with dates/pointers below. Feedback and stored identities remain
                # independent; different verdicts must not be hidden by grouping.
                full = self.event(chat, row["event"])["text"]
                key = (row["role"], digest(normalized(full)), row["feedback"])
            prior = copies.get(key)
            if prior is not None:
                prior["duplicate_count"] = prior.get("duplicate_count", 0) + 1
                if len(prior.setdefault("duplicate_sources", [])) < 4:
                    prior["duplicate_sources"].append(
                        {"event": row["event"], "offset": row["start"], "ts": row["ts"]}
                    )
                continue
            if len(selected) < limit:
                row["freshness"] = freshness(row)
                selected.append(row)
                copies[key] = row
        return selected

    def import_connectome(
        self, chat: str, path: str | Path, project: str, session: str | None = None
    ):
        self.register(chat, project)
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(path)
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        before = self.db.execute(
            "SELECT count(*) FROM events WHERE chat=? AND role!='generated_memory'",
            (chat,),
        ).fetchone()[0]
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
            "new_events": self.db.execute(
                "SELECT count(*) FROM events WHERE chat=? AND role!='generated_memory'",
                (chat,),
            ).fetchone()[0]
            - before,
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
