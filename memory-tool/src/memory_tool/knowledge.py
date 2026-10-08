"""Source-linked project notes, revisioned Markdown imports, and a browsable tree.

All text is evidence. File revisions replace the current document view, never
the original snapshots. Conflicting independent notes remain independent.
"""

import json
import re
import time
import uuid
from pathlib import Path

from .archive import digest
from .evidence import query_words
from .selection import LABEL_VERSION, freshness, importance


def setup(archive):
    archive.db.executescript("""
    CREATE TABLE IF NOT EXISTS note_sources(chat TEXT NOT NULL,path TEXT NOT NULL,PRIMARY KEY(chat,path));
    CREATE TABLE IF NOT EXISTS note_documents(chat TEXT NOT NULL,path TEXT NOT NULL,revision TEXT NOT NULL,ids TEXT NOT NULL,PRIMARY KEY(chat,path));
    CREATE TABLE IF NOT EXISTS note_index(chat TEXT NOT NULL,id TEXT NOT NULL,hash TEXT NOT NULL,PRIMARY KEY(chat,id));
    CREATE VIRTUAL TABLE IF NOT EXISTS note_passages USING fts5(text,chat UNINDEXED,note UNINDEXED);
    CREATE TABLE IF NOT EXISTS knowledge_nodes(chat TEXT NOT NULL,id TEXT NOT NULL,record TEXT NOT NULL,PRIMARY KEY(chat,id));
    """)


def label_key(note):
    return "note:" + digest(json.dumps([note.get("memory_kind"), note["text"]]))


def save_note(
    archive,
    chat,
    text,
    *,
    memory_kind="finding",
    sources=(),
    supersedes=(),
    expires_at=None,
    session=None,
):
    archive.project(chat)
    if not isinstance(text, str) or not text.strip() or len(text) > 8000:
        raise ValueError("Note text must contain 1..8000 characters")
    if memory_kind not in {"finding", "decision", "preference", "commitment", "status"}:
        raise ValueError("Unknown note kind")
    if memory_kind == "status" and not expires_at:
        raise ValueError("Status notes need expires_at")
    from .selection import timestamp

    if expires_at and timestamp(expires_at) is None:
        raise ValueError("Invalid note expiry")
    if len(sources) > 16 or len(supersedes) > 16:
        raise ValueError("At most 16 source or supersedes references")
    for source in sources:
        archive.zoom(chat, source, budget=128)
    for identity in supersedes:
        if not archive.db.execute(
            "SELECT 1 FROM notes WHERE chat=? AND id=?", (chat, identity)
        ).fetchone():
            raise ValueError("Superseded note does not belong to chat")
    note = {
        "id": uuid.uuid4().hex,
        "kind": "note",
        "type": "fact",
        "text": text,
        "ts": str(time.time_ns()),
        "memory_kind": memory_kind,
        "scope": "session" if memory_kind == "status" else "project",
        "sources": list(sources),
        "supersedes": list(supersedes),
    }
    if session:
        note["session_id"] = session
    if expires_at:
        note["expires_at"] = expires_at
    with archive.db:
        archive.db.execute(
            "INSERT INTO notes VALUES(?,?,?)", (chat, note["id"], json.dumps(note))
        )
    refresh_index(archive, chat)
    return {"event": "note:" + note["id"], "record": note}


def sections(text):
    """Exact source slices; headings aid retrieval without rewriting evidence."""
    boundaries = [0] + [
        m.start() for m in re.finditer(r"(?m)^#{1,6} ", text) if m.start()
    ]
    boundaries.append(len(text))
    for start, end in zip(boundaries, boundaries[1:]):
        while start < end:
            stop = min(end, start + 4000)
            if stop < end:
                newline = text.rfind("\n", start + 2000, stop)
                if newline >= 0:
                    stop = newline + 1
            if text[start:stop].strip():
                yield start, text[start:stop]
            start = stop


def sync_markdown(archive, chat, source=None):
    """Bounded, local indexing. Only project md_archive and explicitly added dirs.

    Symlinks and paths outside the project are excluded. A failed/partial scan
    never retracts missing files. Changed files replace their head atomically.
    """
    project = Path(archive.project(chat)).resolve()
    if source is not None:
        path = Path(source).absolute()
        if (
            path.is_symlink()
            or not path.resolve().is_relative_to(project)
            or not (path.is_dir() or path.is_file() and path.suffix == ".jsonl")
        ):
            raise ValueError(
                "Note source must be a directory or notes JSONL inside the project"
            )
        with archive.db:
            archive.db.execute(
                "INSERT OR IGNORE INTO note_sources VALUES(?,?)",
                (chat, str(path.resolve())),
            )
    roots = {project / "md_archive"}
    roots.update(
        Path(r[0])
        for r in archive.db.execute(
            "SELECT path FROM note_sources WHERE chat=?", (chat,)
        )
    )
    report = {
        "files": 0,
        "new_notes": 0,
        "unchanged": 0,
        "skipped": 0,
        "errors": [],
        "incomplete": False,
    }
    remaining = 4 * 1024 * 1024
    for root in sorted(roots):
        if root.is_symlink() or not root.resolve().is_relative_to(project):
            report["skipped"] += 1
            continue
        if root.is_file() and root.suffix == ".jsonl":
            # Explicitly registered legacy notes may have no project or ID.
            # Bind them only to this chosen chat, retaining original provenance.
            try:
                if root.stat().st_size > 1024 * 1024:
                    report["skipped"] += 1
                    continue
                for number, line in enumerate(root.read_text().splitlines(), 1):
                    if not line.strip():
                        continue
                    record = json.loads(line)
                    if not isinstance(record, dict) or not isinstance(
                        record.get("text"), str
                    ):
                        continue
                    if (
                        record.get("project")
                        and Path(record["project"]).resolve() != project
                    ):
                        continue
                    identity = "legacy-" + digest(json.dumps([str(root), line]))
                    note = {
                        "id": identity,
                        "kind": "note",
                        "type": record.get("type", "fact"),
                        "text": record["text"],
                        "ts": record.get("ts"),
                        "scope": "project",
                        "legacy_source": str(root),
                        "source_line": number,
                        "provenance": "Explicitly imported legacy note; original scope may be unspecified.",
                    }
                    with archive.db:
                        report["new_notes"] += archive.db.execute(
                            "INSERT OR IGNORE INTO notes VALUES(?,?,?)",
                            (chat, identity, json.dumps(note)),
                        ).rowcount
                report["files"] += 1
            except (OSError, ValueError) as error:
                report["errors"].append(
                    {"path": str(root), "error": type(error).__name__}
                )
            continue
        # Missing directories are not evidence that every document was deleted.
        if not root.is_dir():
            continue
        seen_paths = set()
        for path in sorted(root.rglob("*.md")):
            if report["files"] >= 128 or remaining <= 0:
                report["incomplete"] = True
                break
            if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
                report["skipped"] += 1
                continue
            seen_paths.add(str(path.resolve()))
            try:
                if path.stat().st_size > 256 * 1024:
                    report["skipped"] += 1
                    continue
                data = path.read_bytes()
                remaining -= len(data)
                report["files"] += 1
                if len(data) > 256 * 1024 or remaining < 0:
                    report["incomplete"] = True
                    continue
                text = data.decode("utf-8")
                key, revision = str(path.resolve()), digest(text)
                old = archive.db.execute(
                    "SELECT revision,ids FROM note_documents WHERE chat=? AND path=?",
                    (chat, key),
                ).fetchone()
                if old and old["revision"] == revision:
                    report["unchanged"] += 1
                    continue
                ids = []
                with archive.db:
                    for offset, chunk in sections(text):
                        identity = "md-" + digest(json.dumps([key, revision, offset]))
                        record = {
                            "id": identity,
                            "kind": "note",
                            "type": "fact",
                            "scope": "project",
                            "text": chunk,
                            "ts": str(path.stat().st_mtime),
                            "document": key,
                            "revision": revision,
                            "source_offset": offset,
                            "source_line": text.count("\n", 0, offset) + 1,
                        }
                        archive.db.execute(
                            "INSERT OR IGNORE INTO notes VALUES(?,?,?)",
                            (chat, identity, json.dumps(record)),
                        )
                        ids.append(identity)
                    archive.db.execute(
                        "INSERT OR REPLACE INTO note_documents VALUES(?,?,?,?)",
                        (chat, key, revision, json.dumps(ids)),
                    )
                report["new_notes"] += len(ids)
            except (OSError, UnicodeError) as error:
                report["errors"].append(
                    {"path": str(path), "error": type(error).__name__}
                )
        if not report["incomplete"] and not report["errors"]:
            # A completed scan can retire removed files in this watched root.
            # Original notes/tree nodes remain readable by their stable IDs.
            with archive.db:
                for row in archive.db.execute(
                    "SELECT path FROM note_documents WHERE chat=?", (chat,)
                ).fetchall():
                    if (
                        Path(row["path"]).is_relative_to(root)
                        and row["path"] not in seen_paths
                        and not Path(row["path"]).exists()
                    ):
                        archive.db.execute(
                            "UPDATE note_documents SET revision='removed',ids='[]' WHERE chat=? AND path=?",
                            (chat, row["path"]),
                        )
    refresh_index(archive, chat)
    return report


def current_notes(archive, chat, notes):
    heads = {
        r["path"]: set(json.loads(r["ids"]))
        for r in archive.db.execute(
            "SELECT * FROM note_documents WHERE chat=?", (chat,)
        )
    }
    labels = {
        r["dup"]: json.loads(r["vector"])
        for r in archive.db.execute(
            "SELECT dup,vector FROM labels WHERE version=? AND dup LIKE 'note:%'",
            (LABEL_VERSION,),
        )
    }
    feedback = {}
    for row in archive.db.execute(
        "SELECT dup,verdict,note FROM feedback WHERE chat=? AND dup LIKE 'note:%' ORDER BY ts",
        (chat,),
    ):
        feedback[row["dup"]] = row["verdict"] + ":" + row["note"]
    return [
        {
            **n,
            "labels": labels.get(label_key(n)),
            "feedback": feedback.get("note:" + n["id"]),
        }
        for n in notes
        if not n.get("document") or n["id"] in heads.get(n["document"], set())
    ]


def refresh_index(archive, chat):
    notes = archive.active_notes(chat)
    previous = {
        r["id"]: r["hash"]
        for r in archive.db.execute(
            "SELECT id,hash FROM note_index WHERE chat=?", (chat,)
        )
    }
    with archive.db:
        for note in notes:
            identity, fingerprint = note["id"], digest(note["text"])
            if previous.pop(identity, None) == fingerprint:
                continue
            archive.db.execute(
                "DELETE FROM note_passages WHERE chat=? AND note=?", (chat, identity)
            )
            archive.db.execute(
                "INSERT INTO note_passages VALUES(?,?,?)",
                (note["text"], chat, identity),
            )
            archive.db.execute(
                "INSERT OR REPLACE INTO note_index VALUES(?,?,?)",
                (chat, identity, fingerprint),
            )
        for identity in previous:
            archive.db.execute(
                "DELETE FROM note_passages WHERE chat=? AND note=?", (chat, identity)
            )
            archive.db.execute(
                "DELETE FROM note_index WHERE chat=? AND id=?", (chat, identity)
            )


def note_candidates(archive, chat, query, limit=8):
    refresh_index(archive, chat)
    words = query_words(query)
    if not words:
        return []
    match = " OR ".join('"' + word.replace('"', '""') + '"' for word in words)
    notes = {n["id"]: n for n in archive.active_notes(chat)}
    rows = archive.db.execute(
        "SELECT note FROM note_passages WHERE note_passages MATCH ? AND chat=? ORDER BY bm25(note_passages) LIMIT ?",
        (match, chat, limit * 4),
    ).fetchall()
    result = []
    from .tool_output import excerpt

    for row in rows:
        n = notes[row["note"]]
        result.append(
            {
                "event": "note:" + n["id"],
                "start": 0,
                "role": "curated_note",
                "kind": "curated_note",
                "ts": n.get("ts"),
                "text": excerpt(n["text"], words) or n["text"][:1024],
                "freshness": freshness(n),
                "feedback": n.get("feedback"),
                "labels": n.get("labels"),
                "presentation": "note excerpt; offset zero zooms the complete source record",
                "source": {
                    k: n[k]
                    for k in ("document", "revision", "source_line", "sources")
                    if k in n
                },
            }
        )
    # Keep stale notes searchable, but don't let them crowd active evidence out.
    result.sort(
        key=lambda r: (
            r["freshness"] in {"expired", "unverified_status"},
            (r.get("feedback") or "").split(":")[0] in {"noise", "stale", "wrong"},
            -(importance(r) if importance(r) is not None else 0.5),
        )
    )
    return result[:limit]


def note_tree(archive, chat, notes=None):
    """Persistent balanced tree; old roots remain zoomable after document edits."""
    notes = archive.active_notes(chat) if notes is None else notes
    if not notes:
        return None
    notes = sorted(
        notes, key=lambda n: (n.get("document", ""), n.get("source_offset", 0), n["id"])
    )

    def build(items):
        if len(items) == 1:
            n = items[0]
            record = {
                "note": "note:" + n["id"],
                "text": n["text"][:480],
                "count": 1,
                "source": n.get("document"),
                "children": [],
            }
        else:
            mid = len(items) // 2
            children = [build(items[:mid]), build(items[mid:])]
            # Promote useful samples instead of chronology's first/last messages.
            samples = sorted(
                items,
                key=lambda n: importance(n) if importance(n) is not None else 0.5,
                reverse=True,
            )[:8]
            record = {
                "text": " | ".join(n["text"][:160] for n in samples),
                "count": len(items),
                "children": children,
            }
        identity = "knowledge-" + digest(json.dumps(record, sort_keys=True))
        archive.db.execute(
            "INSERT OR IGNORE INTO knowledge_nodes VALUES(?,?,?)",
            (chat, identity, json.dumps(record)),
        )
        return "tree:" + identity

    with archive.db:
        return build(notes)


def tree_zoom(archive, chat, identity):
    row = archive.db.execute(
        "SELECT record FROM knowledge_nodes WHERE chat=? AND id=?", (chat, identity)
    ).fetchone()
    if not row:
        raise ValueError("Knowledge tree does not belong to chat")
    node = json.loads(row[0])
    children = []
    for ref in node["children"]:
        child = json.loads(
            archive.db.execute(
                "SELECT record FROM knowledge_nodes WHERE chat=? AND id=?",
                (chat, ref[5:]),
            ).fetchone()[0]
        )
        children.append({"event": ref, "count": child["count"], "text": child["text"]})
    return json.dumps(
        {
            "evidence": "Historical note tree; excerpts may omit facts. Zoom the note for exact evidence.",
            **node,
            "children": children,
        },
        ensure_ascii=False,
    )


def discover_notes(archive, chat, judge, max_nodes=16):
    """Best-first bounded tree traversal, separate from lexical recall.

    judge receives source-shaped branch rows and returns relevance probabilities.
    Only actual notes become search results; branch excerpts are navigation hints.
    """
    notes = archive.active_notes(chat)
    root = note_tree(archive, chat, notes)
    if not root:
        return [], {"visited": 0, "root": None, "limited": False}
    by_id = {"note:" + n["id"]: n for n in notes}
    queue, found, visited, judged = [(1.0, 0, root)], [], 0, 0
    while queue and visited < max_nodes:
        queue.sort(reverse=True)
        priority, depth, ref = queue.pop(0)
        node = json.loads(
            archive.db.execute(
                "SELECT record FROM knowledge_nodes WHERE chat=? AND id=?",
                (chat, ref[5:]),
            ).fetchone()[0]
        )
        visited += 1
        if node.get("note"):
            n = by_id[node["note"]]
            found.append(
                {
                    "event": node["note"],
                    "start": 0,
                    "role": "curated_note",
                    "kind": "curated_note",
                    "ts": n.get("ts"),
                    "text": n["text"][:4000],
                    "freshness": freshness(n),
                    "feedback": n.get("feedback"),
                    "source": {
                        k: n[k]
                        for k in ("document", "revision", "source_line", "sources")
                        if k in n
                    },
                    "presentation": "tree-discovered note; offset zero zooms the complete source record",
                }
            )
            continue
        rows = []
        for child in node["children"]:
            value = json.loads(
                archive.db.execute(
                    "SELECT record FROM knowledge_nodes WHERE chat=? AND id=?",
                    (chat, child[5:]),
                ).fetchone()[0]
            )
            rows.append(
                {
                    "event": child,
                    "start": 0,
                    "role": "derived_note_tree",
                    "kind": "navigation",
                    "ts": None,
                    "text": value["text"],
                }
            )
        # Each expansion rates two children; the independent cap bounds requests.
        if len(rows) > max_nodes - judged:
            queue.append((priority, depth, ref))
            break
        scores = judge(rows)
        judged += len(rows)
        queue.extend(
            (scores[(r["event"], 0)], depth + 1, r["event"])
            for r in rows
            if scores[(r["event"], 0)] >= 0.5
        )
    return found, {
        "visited": visited,
        "judged": judged,
        "root": root,
        "limited": bool(queue),
    }
