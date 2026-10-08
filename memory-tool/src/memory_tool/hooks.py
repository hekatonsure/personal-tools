"""Native lifecycle capture/checkpoint/restore; optional background Jev labeling."""

from __future__ import annotations

import json
import math
from pathlib import Path
import time

from .archive import Archive, canonical, digest
from .evidence import source_kind
from .packing import checkpoint


def public_item(record):
    if record.get("type") != "response_item":
        return None
    payload = record.get("payload", {})
    kind = payload.get("type")
    if kind == "message":
        parts = [
            p
            for p in payload.get("content", [])
            if p.get("type") not in {"reasoning", "thinking", "redacted_thinking"}
        ]
        payload = {**payload, "content": parts}
        role = payload.get("role", "unknown")
        text = "\n".join(
            p.get("text", json.dumps(p, ensure_ascii=False, separators=(",", ":")))
            for p in parts
        )
        if source_kind(role, text) == "generated":
            role = "generated_memory"
    elif kind in {"function_call", "custom_tool_call"}:
        role, text = (
            "tool_call",
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        )
    elif kind in {"function_call_output", "custom_tool_call_output"}:
        output = payload.get("output", "")
        role, text = (
            "tool_result",
            output
            if isinstance(output, str)
            else json.dumps(output, ensure_ascii=False, separators=(",", ":")),
        )
    else:
        return None
    return (
        role,
        text,
        json.dumps(
            {**record, "payload": payload}, ensure_ascii=False, separators=(",", ":")
        ),
    )


def capture_rollout(archive, chat, event, max_bytes=64 * 1024 * 1024):
    """Read complete JSONL lines only. A crash retries idempotently from the last cursor."""
    session, project = event["session_id"], canonical(event["cwd"])
    if archive.project(chat) != project:
        raise ValueError("Capture project does not match chat")
    path = Path(event["transcript_path"]).resolve()
    archive.db.execute(
        "CREATE TABLE IF NOT EXISTS native_cursors(path TEXT PRIMARY KEY,session TEXT NOT NULL,project TEXT NOT NULL,offset INTEGER NOT NULL)"
    )
    key = canonical(str(path))
    prior = archive.db.execute(
        "SELECT * FROM native_cursors WHERE path=?", (key,)
    ).fetchone()
    if prior and (prior["session"] != session or prior["project"] != project):
        raise ValueError("Transcript cursor identity changed")
    offset = prior["offset"] if prior else 0
    size = path.stat().st_size
    if size < offset:
        raise ValueError("Transcript shrank; refusing cursor reset")
    count = 0
    with path.open("rb") as stream:
        first = json.loads(stream.readline(8 * 1024 * 1024))
        meta = first.get("payload", {})
        if (
            first.get("type") != "session_meta"
            or meta.get("id", meta.get("session_id")) != session
            or canonical(meta.get("cwd", "")) != project
        ):
            raise ValueError("Transcript session/project identity mismatch")
        stream.seek(offset)
        consumed = 0
        while consumed < max_bytes:
            line = stream.readline(min(8 * 1024 * 1024, max_bytes - consumed))
            if not line or not line.endswith(b"\n"):
                break
            record = json.loads(line)
            item = public_item(record)
            if item:
                role, text, raw = item
                ts = record.get("timestamp")
                if not isinstance(ts, str):
                    raise ValueError("Public transcript record lacks stable timestamp")
                source_role = (
                    record.get("payload", {}).get("role", role)
                    if role == "generated_memory"
                    else role
                )
                source = f"native:{session}:{ts}:{source_role}:{digest(text)}"
                archive.append(chat, role, text, source, raw, ts)
                count += 1
            offset += len(line)
            consumed += len(line)
    with archive.db:
        archive.db.execute(
            "INSERT INTO native_cursors VALUES(?,?,?,?) ON CONFLICT(path) DO UPDATE SET offset=excluded.offset",
            (key, session, project, offset),
        )
    return {
        "observed_public_records": count,
        "offset": offset,
        "backlog_bytes": max(0, path.stat().st_size - offset),
    }


def handle(
    archive: Archive, event, budget=24000, recent=8000, connectome=None, backend="codex"
):
    kind = event.get("hook_event_name")
    if kind not in {"PreCompact", "SessionStart", "Stop", "UserPromptSubmit"}:
        return {"continue": True}
    project, session = event.get("cwd"), event.get("session_id")
    if not project or not Path(project).is_absolute() or not session:
        raise ValueError("Hook requires absolute cwd and session_id")
    if backend not in {"codex", "claude"}:
        raise ValueError("Unknown hook backend")
    capture = {"backlog_bytes": None, "missing_transcript": True}
    transcript = event.get("transcript_path")
    if backend == "claude" and transcript and Path(transcript).exists():
        from .claude import capture_transcript

        project, capture = capture_transcript(archive, event)
    chat = archive.chat_for_project(project)
    if (
        kind in {"PreCompact", "SessionStart"}
        and connectome
        and Path(connectome).exists()
    ):
        archive.import_connectome(chat, connectome, project)
    if backend == "codex" and transcript and Path(transcript).exists():
        capture = capture_rollout(archive, chat, event)
    if kind == "Stop":
        from .labels import spawn_labeler

        # Labels for this turn are ready by the next restore; the hook never waits.
        spawn_labeler(archive, chat)
    if kind in {"Stop", "UserPromptSubmit"}:
        return {"continue": True}
    if kind == "SessionStart" and event.get("source") not in {
        "compact",
        "resume",
        "clear",
        "startup",
    }:
        return {"continue": True}
    checkpoint_id, packet = checkpoint(
        archive, chat, budget=budget, recent_budget=recent, session=session
    )
    detail = {
        "backend": backend,
        "checkpoint": checkpoint_id,
        **packet.metadata(),
        "capture": capture,
        "source": event.get("source"),
        "prepared_at": time.time(),
        "receipt_verified": False,
    }
    operation = f"hook:{session}:{kind}"
    archive.operation(
        operation,
        chat,
        session,
        "checkpointed" if kind == "PreCompact" else "prepared",
        detail,
    )
    if kind == "PreCompact":
        return {
            "continue": True,
            **(
                {
                    "systemMessage": "memory-tool: capture incomplete; native transcript retained, checkpoint covers only archived evidence"
                }
                if capture.get("backlog_bytes") != 0
                else {}
            ),
        }
    header = f"Memory restored for project {canonical(project)}. Use memory tools with project={json.dumps(canonical(project))}. Report restored records that are noise, stale or wrong, and anything missing, with memory_feedback. Native compaction retains its own summary.\n"
    # The envelope is part of the same history budget.
    from .packing import Tokens

    effective = budget
    while (
        max(
            Tokens().count(header + packet.text),
            math.ceil(len((header + packet.text).encode("utf-8")) / 4),
        )
        > budget
    ):
        measured = max(
            Tokens().count(header + packet.text),
            math.ceil(len((header + packet.text).encode("utf-8")) / 4),
        )
        effective = max(512, int(effective * budget / measured) - 64)
        if effective == 512:
            raise ValueError("Hook envelope cannot fit requested budget")
        checkpoint_id, packet = checkpoint(
            archive,
            chat,
            budget=effective,
            recent_budget=min(recent, effective // 2),
            session=session,
        )
        detail.update({"checkpoint": checkpoint_id, **packet.metadata()})
        archive.operation(operation, chat, session, "prepared", detail)
    return {
        "continue": True,
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": header + packet.text,
        },
    }


def hook_response(archive, event, **kwargs):
    try:
        return handle(archive, event, **kwargs)
    except Exception as error:
        # Never turn a memory integration failure into lost work or a fake success.
        return {
            "continue": True,
            "systemMessage": f"memory-tool: recovery failed ({type(error).__name__}: {str(error)[:180]}); native session continues",
        }
