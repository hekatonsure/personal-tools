import json

import pytest

from memory_tool.archive import Archive, canonical
from memory_tool.hooks import capture_rollout, handle, hook_response
from memory_tool.mcp import call
from memory_tool.packing import Tokens


def transcript(tmp_path, session="session-1"):
    path = tmp_path / "rollout.jsonl"
    records = [
        {"type": "session_meta", "payload": {"id": session, "cwd": str(tmp_path)}},
        {
            "type": "response_item",
            "timestamp": "2026-10-04T00:00:01Z",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": "RECOVERY_PROOF=plum-49. Use the fast CPU.",
                    }
                ],
            },
        },
        {
            "type": "response_item",
            "timestamp": "2026-10-04T00:00:02Z",
            "payload": {"type": "reasoning", "text": "not public"},
        },
        {
            "type": "response_item",
            "timestamp": "2026-10-04T00:00:03Z",
            "payload": {"type": "custom_tool_call_output", "output": "result exit0"},
        },
    ]
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return path


def test_native_compact_restore_captures_once_without_hidden_reasoning(tmp_path):
    path = transcript(tmp_path)
    a = Archive(tmp_path / "memory.sqlite")
    h = {
        "cwd": str(tmp_path),
        "session_id": "session-1",
        "transcript_path": str(path),
        "hook_event_name": "PreCompact",
    }
    assert handle(a, h) == {"continue": True}
    chat = a.chat_for_project(str(tmp_path))
    assert len(a.events(chat)) == 2
    restored = handle(
        a,
        {**h, "hook_event_name": "SessionStart", "source": "compact"},
        budget=1500,
        recent=500,
    )
    text = restored["hookSpecificOutput"]["additionalContext"]
    assert "plum-49" in text and "not public" not in text
    assert Tokens().count(text) <= 1500
    assert len(a.events(chat)) == 2
    status = call(a, chat, "memory_status", {})
    assert status["native_recovery"]["state"] == "prepared"
    assert status["native_recovery"]["receipt_verified"] is False
    assert status["native_recovery"]["capture"]["backlog_bytes"] == 0
    a.close()


def test_identity_and_partial_lines_fail_without_false_recovery(tmp_path):
    path = transcript(tmp_path)
    a = Archive(tmp_path / "memory.sqlite")
    a.register("main", str(tmp_path))
    h = {
        "cwd": str(tmp_path),
        "session_id": "wrong-session",
        "transcript_path": str(path),
        "hook_event_name": "PreCompact",
    }
    with pytest.raises(ValueError, match="identity mismatch"):
        capture_rollout(a, "main", h)
    result = hook_response(a, h)
    assert result["continue"] and "recovery failed" in result["systemMessage"]
    assert "hookSpecificOutput" not in result
    h["session_id"] = "session-1"
    with path.open("ab") as stream:
        stream.write(b'{"type":"response_item"')
    first = capture_rollout(a, "main", h)
    second = capture_rollout(a, "main", h)
    assert first["offset"] == second["offset"]
    assert first["backlog_bytes"] > 0 and len(a.events("main")) == 2
    path.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="shrank"):
        capture_rollout(a, "main", h)
    a.close()


def test_project_routing_never_crosses_scopes(tmp_path):
    a = Archive(tmp_path / "memory.sqlite")
    a.register("bff-master", str(tmp_path / "bff"))
    other = a.chat_for_project(str(tmp_path / "other"))
    first = a.append("bff-master", "user", "budget apple")
    second = a.append(other, "user", "budget pear")
    result = call(
        a,
        "bff-master",
        "memory_search",
        {"query": "budget", "project": str(tmp_path / "other")},
    )
    assert result["project"] == canonical(str(tmp_path / "other"))
    assert [h["event"] for h in result["hits"]] == [second]
    with pytest.raises(ValueError, match="belong"):
        call(
            a,
            "bff-master",
            "memory_zoom",
            {"event": first, "project": str(tmp_path / "other")},
        )
    a.close()


def test_restore_refreshes_retracted_notes(tmp_path):
    path = transcript(tmp_path)
    a = Archive(tmp_path / "memory.sqlite")
    chat = a.chat_for_project(str(tmp_path))
    a.db.execute(
        "INSERT INTO notes VALUES(?,?,?)",
        (chat, "n1", json.dumps({"id": "n1", "type": "pin", "text": "STALE_LIMIT"})),
    )
    a.db.commit()
    h = {
        "cwd": str(tmp_path),
        "session_id": "session-1",
        "transcript_path": str(path),
        "hook_event_name": "PreCompact",
    }
    handle(a, h)
    a.db.execute(
        "INSERT INTO notes VALUES(?,?,?)",
        (
            chat,
            "n2",
            json.dumps({"id": "n2", "kind": "retraction", "retracts": ["n1"]}),
        ),
    )
    a.db.commit()
    restored = handle(a, {**h, "hook_event_name": "SessionStart", "source": "compact"})
    assert "STALE_LIMIT" not in restored["hookSpecificOutput"]["additionalContext"]
    a.close()


def test_restored_hook_envelope_is_not_recursively_repacked(tmp_path):
    path = transcript(tmp_path)
    a = Archive(tmp_path / "memory.sqlite")
    event = {
        "cwd": str(tmp_path),
        "session_id": "session-1",
        "transcript_path": str(path),
        "hook_event_name": "SessionStart",
        "source": "compact",
    }
    first = handle(a, event)
    text = first["hookSpecificOutput"]["additionalContext"]
    replay = {
        "type": "response_item",
        "timestamp": "2026-10-04T00:00:04Z",
        "payload": {
            "type": "message",
            "role": "developer",
            "content": [{"type": "input_text", "text": text}],
        },
    }
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(replay) + "\n")
    second = handle(a, event)
    chat = a.chat_for_project(str(tmp_path))
    assert len(a.events(chat)) == 2
    assert len(second["hookSpecificOutput"]["additionalContext"]) == len(text)
    a.close()
