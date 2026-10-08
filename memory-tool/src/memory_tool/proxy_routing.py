"""Route native Codex requests using local thread identity, never prompt text."""

from collections import OrderedDict
import json
from pathlib import Path
import sqlite3
import threading
import uuid

from .archive import Archive, canonical
from .compaction import unique_object


def thread_id(headers, body):
    """Accept consistent UUID identities from Codex's protocol metadata."""
    value = json.loads(body, object_pairs_hook=unique_object)
    metadata = value.get("client_metadata", {})
    candidates = []
    for source in (headers, metadata):
        for key in ("thread_id", "session_id", "session-id"):
            if source.get(key):
                candidates.append(source[key])
        raw = source.get("x-codex-turn-metadata")
        if raw:
            parsed = json.loads(raw, object_pairs_hook=unique_object)
            candidates.extend(
                parsed[k] for k in ("thread_id", "session_id") if parsed.get(k)
            )
    identities = {str(uuid.UUID(candidate)) for candidate in candidates}
    return identities.pop() if len(identities) == 1 else None


class CodexRouter:
    """Bounded per-thread state; unknown/ambiguous identities never share a chat."""

    def __init__(self, db, codex_home, factory, max_cached=128):
        self.db, self.home, self.factory = db, Path(codex_home), factory
        self.max_cached = max_cached
        self.cache = OrderedDict()
        self.lock = threading.Lock()
        self.routed = self.unrouted = 0
        archive = Archive(db)
        try:
            archive.db.execute(
                "CREATE TABLE IF NOT EXISTS proxy_routes(thread TEXT PRIMARY KEY,chat TEXT NOT NULL)"
            )
            archive.db.commit()
        finally:
            archive.close()

    def project(self, session, archive):
        # Use only the newest native registry; never infer cwd from model input.
        paths = sorted(
            (p for p in self.home.glob("state_*.sqlite") if p.stem[6:].isdigit()),
            key=lambda p: int(p.stem[6:]),
            reverse=True,
        )
        if paths:
            with sqlite3.connect(
                paths[0].resolve().as_uri() + "?mode=ro", uri=True, timeout=1
            ) as db:
                row = db.execute(
                    "SELECT cwd FROM threads WHERE id=?", (session,)
                ).fetchone()
            if row and Path(row[0]).is_absolute():
                return canonical(row[0])
        # Hooks can establish identity before the native registry is populated.
        rows = archive.db.execute(
            "SELECT DISTINCT c.project FROM operations o JOIN chats c ON c.id=o.chat "
            "WHERE o.thread=? AND o.id LIKE 'hook:%'",
            (session,),
        ).fetchall()
        return rows[0][0] if len(rows) == 1 else None

    def resolve(self, headers, body, encoding="identity"):
        route = None
        try:
            if encoding not in {"", "identity"}:
                return None
            session = thread_id(headers, body)
            if session is None:
                return None
            archive = Archive(self.db)
            try:
                project = self.project(session, archive)
                if project is None:
                    return None
                chat = archive.chat_for_project(project)
                with archive.db:
                    archive.db.execute(
                        "INSERT OR IGNORE INTO proxy_routes VALUES(?,?)",
                        (session, chat),
                    )
                bound = archive.db.execute(
                    "SELECT chat FROM proxy_routes WHERE thread=?", (session,)
                ).fetchone()[0]
                if bound != chat:
                    return (
                        None  # A thread changing project needs an explicit new scope.
                    )
            finally:
                archive.close()
            with self.lock:
                key = (chat, session)
                if key not in self.cache:
                    self.cache[key] = self.factory(chat, session)
                self.cache.move_to_end(key)
                route = (chat, session, self.cache[key])
                while len(self.cache) > self.max_cached:
                    self.cache.popitem(last=False)
            return route
        except (
            ValueError,
            TypeError,
            AttributeError,
            KeyError,
            sqlite3.Error,
            OSError,
        ):
            return None
        finally:
            with self.lock:
                if route is None:
                    self.unrouted += 1
                else:
                    self.routed += 1

    def status(self):
        with self.lock:
            return {
                "routed_requests": self.routed,
                "unrouted_requests": self.unrouted,
                "cached_threads": len(self.cache),
            }
