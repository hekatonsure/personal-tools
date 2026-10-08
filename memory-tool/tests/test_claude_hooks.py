"""Offline native Claude fixtures. Never starts Claude or uses model quota."""

import json
import os
import subprocess
import sys

import pytest

from memory_tool.archive import Archive, canonical
from memory_tool.claude import capture_transcript
from memory_tool.hooks import handle, hook_response
from memory_tool.mcp import call
from memory_tool.packing import Tokens


def record(
    tmp_path,
    identity="u1",
    role="user",
    content="Keep the plum-49 recovery marker.",
    **extra,
):
    return {
        "type": role,
        "uuid": identity,
        "sessionId": "claude-session",
        "cwd": str(tmp_path),
        "timestamp": "2026-10-07T18:00:00Z",
        "message": {"role": role, "content": content},
        **extra,
    }


def write_records(path, records, mode="w"):
    with path.open(mode, encoding="utf-8") as stream:
        for value in records:
            stream.write(json.dumps(value) + "\n")


def setup(tmp_path):
    path = tmp_path / "claude-session.jsonl"
    write_records(
        path,
        [
            {"type": "file-history-snapshot", "snapshot": {}},
            record(tmp_path),
            record(
                tmp_path,
                "a1",
                "assistant",
                [
                    {
                        "type": "thinking",
                        "thinking": "HIDDEN_THOUGHT",
                        "signature": "HIDDEN_SIGNATURE",
                    },
                    {"type": "redacted_thinking", "data": "HIDDEN_REDACTED"},
                    {"type": "text", "text": "Saved the marker."},
                    {
                        "type": "tool_use",
                        "id": "t1",
                        "name": "Read",
                        "input": {"file_path": "/example"},
                    },
                ],
            ),
            record(
                tmp_path,
                "u2",
                content=[
                    {
                        "type": "tool_result",
                        "tool_use_id": "t1",
                        "content": "file result Ω",
                    },
                ],
            ),
        ],
    )
    archive = Archive(tmp_path / "memory.sqlite")
    event = {
        "cwd": str(tmp_path),
        "session_id": "claude-session",
        "transcript_path": str(path),
        "hook_event_name": "PreCompact",
    }
    return archive, path, event


def test_claude_capture_restore_search_and_zoom(tmp_path):
    archive, path, event = setup(tmp_path)
    assert handle(archive, event, backend="claude") == {"continue": True}
    chat = archive.chat_for_project(str(tmp_path))
    rows = archive.events(chat)
    assert [r["role"] for r in rows] == [
        "user",
        "assistant",
        "tool_call",
        "tool_result",
    ]
    assert all(r["ts"] == "2026-10-07T18:00:00Z" for r in rows)
    assert "HIDDEN_" not in json.dumps(
        [dict(r) for r in archive.db.execute("SELECT * FROM events")]
    )
    restored = handle(
        archive,
        {**event, "hook_event_name": "SessionStart", "source": "compact"},
        backend="claude",
        budget=1500,
        recent=500,
    )
    text = restored["hookSpecificOutput"]["additionalContext"]
    assert "plum-49" in text and Tokens().count(text) <= 1500
    assert len(archive.events(chat)) == 4
    status = call(archive, chat, "memory_status", {})["native_recovery"]
    assert status["backend"] == "claude" and status["state"] == "prepared"
    assert (
        status["receipt_verified"] is False and status["capture"]["backlog_bytes"] == 0
    )
    found = call(archive, chat, "memory_search", {"query": "plum-49"})["hits"]
    assert found[0]["event"] == rows[0]["id"]
    assert (
        call(archive, chat, "memory_zoom", {"event": rows[-1]["id"]})["text"]
        == rows[-1]["text"]
    )
    archive.close()


def test_changed_cwd_keeps_original_project_and_sessions_stay_scoped(tmp_path):
    archive, path, event = setup(tmp_path)
    other_cwd = tmp_path / "other-project"
    write_records(path, [record(other_cwd, "u3", content="Use the violet build.")], "a")
    moved = {**event, "cwd": str(other_cwd), "hook_event_name": "Stop"}
    handle(archive, moved, backend="claude")
    chat = archive.chat_for_project(str(tmp_path))
    assert len(archive.events(chat)) == 5
    archive.append(chat, "user", "FOREIGN_SESSION_MARKER", "claude:other-session:u1:0")
    archive.append(
        chat, "user", "CODEX_SESSION_MARKER", "native:other-codex:time:user:hash"
    )
    restored = handle(
        archive,
        {**moved, "hook_event_name": "SessionStart", "source": "resume"},
        backend="claude",
    )
    text = restored["hookSpecificOutput"]["additionalContext"]
    assert f"project {canonical(str(tmp_path))}." in text
    assert (
        "violet" in text
        and "FOREIGN_SESSION_MARKER" not in text
        and "CODEX_SESSION_MARKER" not in text
    )
    assert archive.db.execute("SELECT COUNT(*) FROM chats").fetchone()[0] == 1
    archive.close()


def test_partial_lines_and_bounded_capture_retry_without_duplicates(tmp_path):
    archive, path, event = setup(tmp_path)
    data = path.read_bytes()
    first_public_end = data.index(b"\n", data.index(b"\n") + 1) + 1
    project, first = capture_transcript(archive, event, max_bytes=first_public_end)
    assert first["offset"] == first_public_end and first["backlog_bytes"] > 0
    extra = json.dumps(record(tmp_path, "u4", content="Last piece.")) + "\n"
    with path.open("ab") as stream:
        stream.write(extra[:30].encode())
    _, second = capture_transcript(archive, event)
    _, third = capture_transcript(archive, event)
    assert second["offset"] == third["offset"] == len(data)
    assert third["backlog_bytes"] == 30
    with path.open("ab") as stream:
        stream.write(extra[30:].encode())
    _, complete = capture_transcript(archive, event)
    assert complete["backlog_bytes"] == 0
    assert len(archive.events(archive.chat_for_project(project))) == 5
    archive.close()


def test_compaction_rewrite_rescans_uuid_and_excludes_generated_summaries(tmp_path):
    archive, path, event = setup(tmp_path)
    handle(archive, event, backend="claude")
    summary = record(
        tmp_path, "summary", content="SYNTHETIC_COMPACT_SUMMARY", isCompactSummary=True
    )
    write_records(path, [record(tmp_path), summary])
    restored = handle(
        archive,
        {**event, "hook_event_name": "SessionStart", "source": "compact"},
        backend="claude",
    )
    text = restored["hookSpecificOutput"]["additionalContext"]
    assert "plum-49" in text and "SYNTHETIC_COMPACT_SUMMARY" not in text
    chat = archive.chat_for_project(str(tmp_path))
    assert len(archive.events(chat)) == 4
    assert call(archive, chat, "memory_status", {})["native_recovery"]["capture"][
        "rescanned"
    ]
    # The restored hook output and meta messages must never become user decisions.
    write_records(
        path,
        [
            record(
                tmp_path,
                "restore",
                content="<system-reminder>\n" + text + "</system-reminder>",
                isMeta=True,
            ),
            record(tmp_path, "meta", content="Scaffolding message", isMeta=True),
        ],
        "a",
    )
    handle(archive, {**event, "hook_event_name": "Stop"}, backend="claude")
    rows = archive.events(chat)
    assert len(rows) == 5 and rows[-1]["role"] == "developer"
    assert "MEMORY-TOOL/v1" not in json.dumps(rows)
    archive.close()


@pytest.mark.parametrize(
    "change",
    [
        {"sessionId": "other-session"},
        {"cwd": "relative/path"},
        {"timestamp": None},
        {"uuid": None},
        {"isSidechain": True},
    ],
)
def test_invalid_identity_fails_open_without_false_recovery(tmp_path, change):
    archive, path, event = setup(tmp_path)
    write_records(path, [record(tmp_path, **change)])
    result = hook_response(archive, event, backend="claude")
    assert result["continue"] and "recovery failed" in result["systemMessage"]
    assert "hookSpecificOutput" not in result
    assert archive.db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    archive.close()


def test_cursor_cannot_be_reused_for_another_session(tmp_path):
    archive, path, event = setup(tmp_path)
    capture_transcript(archive, event)
    with pytest.raises(ValueError, match="cursor identity changed"):
        capture_transcript(archive, {**event, "session_id": "different"})
    archive.close()


def test_startup_without_transcript_and_empty_transcript(tmp_path):
    archive, path, event = setup(tmp_path)
    path.unlink()
    event = {**event, "hook_event_name": "SessionStart", "source": "startup"}
    assert "hookSpecificOutput" in handle(archive, event, backend="claude")
    path.touch()
    assert "hookSpecificOutput" in handle(archive, event, backend="claude")
    assert archive.db.execute("SELECT COUNT(*) FROM claude_cursors").fetchone()[0] == 0
    archive.close()


def test_cli_hook_uses_claude_parser_without_starting_claude(tmp_path):
    archive, path, event = setup(tmp_path)
    db = archive.path
    archive.close()
    env = {**os.environ, "MEMORY_TOOL_AUTO_LABEL": "0"}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "memory_tool.cli",
            "--db",
            str(db),
            "hook",
            "--backend",
            "claude",
        ],
        input=json.dumps(
            {**event, "hook_event_name": "SessionStart", "source": "resume"}
        ),
        text=True,
        capture_output=True,
        env=env,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert (
        "plum-49"
        in json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    )
