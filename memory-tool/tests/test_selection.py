import json

import pytest

from memory_tool.archive import Archive
from memory_tool.hooks import handle
from memory_tool.packing import Tokens, build_packet
from memory_tool.retrieval import search
from memory_tool.selection import focus_terms, select_notes, timestamp

NOW = timestamp("2026-10-04T22:00:00Z")


def test_conversational_followups_do_not_replace_topic_with_filler():
    events = [
        {"role": "user", "text": text, "source": f"native:a:{i}"}
        for i, text in enumerate(
            [
                "Work on cleaner retrieval and desktop recovery.",
                "great! how is it? final version review",
                "how does it feel now?",
                "how might we do that?",
                "work on all of that at the same time please",
            ]
        )
    ]
    terms = focus_terms(events, "a")
    assert {"retrieval", "desktop", "recovery"} <= set(terms)
    assert not {"all", "same", "time", "might", "feel"} & set(terms)


def note(id, text, **extra):
    return {
        "id": id,
        "text": text,
        "ts": "2026-10-04T21:00:00Z",
        "session_id": "a",
        **extra,
    }


def test_status_expiry_does_not_expire_preferences_or_originals(tmp_path):
    rows = [
        note(
            "old",
            "memory process is currently running PID 9371",
            ts="2026-10-03T20:00:00Z",
        ),
        note("durable", "Always use uv", memory_kind="preference", ts="2020-01-01"),
        note(
            "status_words",
            "Never overwrite a currently running process",
            memory_kind="preference",
            ts="2020-01-01",
        ),
        note(
            "expired_preference",
            "Temporary memory preference",
            memory_kind="preference",
            expires_at="2020-01-01",
        ),
        note("future", "memory diagnostic status", expires_at="2026-10-04T23:00:00Z"),
        note("unknown", "memory restart required", ts=None),
    ]
    selected, _, omitted = select_notes(rows, ["memory"], "a", NOW)
    assert {r["id"] for r in selected} == {"durable", "status_words", "future"}
    assert omitted == {"expired": 2, "unverified_status": 1}
    a = Archive(tmp_path / "archive.sqlite")
    a.register("main", str(tmp_path))
    for row in rows:
        a.db.execute(
            "INSERT INTO notes VALUES(?,?,?)", ("main", row["id"], json.dumps(row))
        )
    a.db.commit()
    assert json.loads(a.zoom("main", "note:old")["text"]) == rows[0]
    a.register("other", str(tmp_path / "other"))
    with pytest.raises(ValueError, match="belong"):
        a.zoom("other", "note:old")
    a.close()


def test_cli_accepts_both_note_and_event_zoom_references():
    from memory_tool.cli import parser

    assert (
        parser().parse_args(["zoom", "--chat", "main", "note:old"]).event == "note:old"
    )
    assert parser().parse_args(["zoom", "--chat", "main", "42"]).event == "42"


def test_explicit_correction_preserves_both_sources_and_ambiguous_conflicts():
    rows = [
        note("before", "memory restart required", ts="2026-10-04T20:00:00Z"),
        note("after", "memory restart verified; no further restart"),
        note(
            "port1",
            "memory listener port 8000",
            claims=[{"key": "port", "value": "8000"}],
        ),
        note(
            "port2",
            "memory listener port 8001",
            ts="2026-10-04T21:10:00Z",
            claims=[{"key": "port", "value": "8001"}],
        ),
    ]
    selected, resolutions, omitted = select_notes(rows, ["memory"], "a", NOW)
    assert {r["id"] for r in selected} == {"after", "port1", "port2"}
    restart = next(r for r in resolutions if r["key"] == "restart:a")
    assert restart["state"] == "resolved" and restart["value"] == "verified"
    assert [r["note"] for r in restart["sources"]] == ["before", "after"]
    port = next(r for r in resolutions if r["key"] == "port")
    assert port["state"] == "unresolved" and port["value"] is None
    assert omitted["corrected"] == 1


def test_same_timestamp_or_another_session_is_not_an_implicit_correction():
    rows = [
        note("before", "memory restart required"),
        note("after", "memory restart verified"),
    ]
    _, resolutions, _ = select_notes(rows, ["memory"], "a", NOW)
    assert resolutions[0]["state"] == "unresolved"
    rows[1]["session_id"] = "b"
    _, resolutions, _ = select_notes(rows, ["memory"], None, NOW)
    assert len(resolutions) == 2
    assert {r["value"] for r in resolutions} == {"required", "verified"}


def test_questions_and_hypotheses_never_resolve_state():
    rows = [
        note("old", "memory restart required"),
        note("question", "Can we check whether the memory restart worked?"),
    ]
    _, resolutions, _ = select_notes(rows, ["memory"], "a", NOW)
    assert len(resolutions) == 1 and resolutions[0]["value"] == "required"


def test_older_user_decision_survives_changed_topic_vocabulary(tmp_path):
    a = Archive(tmp_path / "memory.sqlite")
    a.register("main", str(tmp_path))
    a.append("main", "user", "Use 160000 as the compaction threshold.", "native:a:0")
    for i in range(100):
        a.append(
            "main",
            "tool_result",
            f"Build {i} " + ("miscellaneous bytes " * 80),
            f"native:a:{i + 1}",
        )
    a.append("main", "user", "Continue retrieval and restoration.", "native:a:last")
    packet = build_packet(a, "main", budget=12000, recent_budget=8000, session="a")
    assert "160000" in packet.text
    a.close()


def test_session_filter_rebuilds_tree_and_hook_uses_actual_session(tmp_path):
    a = Archive(tmp_path / "archive.sqlite")
    a.register("main", str(tmp_path))
    for i in range(100):
        for session in ("a", "b"):
            a.append(
                "main",
                "user",
                f"{session.upper()}_ONLY decision {i} " + "work " * 25,
                f"native:{session}:{i}",
            )
    first = build_packet(a, "main", 1500, 500, session="a")
    second = build_packet(a, "main", 1500, 500, session="b")
    assert "A_ONLY" in first.text and "B_ONLY" not in first.text
    assert "B_ONLY" in second.text and "A_ONLY" not in second.text
    assert first.watermark == second.watermark
    restored = handle(
        a,
        {
            "cwd": str(tmp_path),
            "session_id": "a",
            "hook_event_name": "SessionStart",
            "source": "resume",
        },
        1500,
        500,
    )
    assert "B_ONLY" not in restored["hookSpecificOutput"]["additionalContext"]
    assert a.search("main", "B_ONLY")
    a.close()


def test_mechanical_duplicates_group_but_document_revisions_survive(tmp_path):
    a = Archive(tmp_path / "archive.sqlite")
    a.register("main", str(tmp_path))
    text = "Caching performance decision: retain context. " * 8
    first = a.append(
        "main", "tool_result", json.dumps({"output": text, "wall_time_seconds": 1})
    )
    duplicate = a.append(
        "main",
        "tool_result",
        json.dumps(
            [
                {
                    "type": "text",
                    "text": json.dumps({"output": text, "wall_time_seconds": 2}),
                }
            ]
        ),
    )
    changed = a.append(
        "main",
        "tool_result",
        json.dumps({"output": text + "Correction: disable caching now."}),
    )
    rows = a.search("main", "Caching performance decision")
    assert len(rows) == 2
    grouped = next(r for r in rows if r["event"] in {first, duplicate})
    assert grouped["duplicate_count"] == 1
    assert any(r["event"] == changed for r in rows)
    assert a.zoom("main", duplicate)["text"].startswith("[")
    result = search(a, "main", "Caching performance decision")
    assert len(result["hits"]) == 2 and any(
        r["duplicate_count"] == 1 for r in result["hits"]
    )
    a.close()


def test_equal_output_with_different_exit_status_or_payload_is_not_grouped(tmp_path):
    a = Archive(tmp_path / "archive.sqlite")
    a.register("main", str(tmp_path))
    for extra in (
        {"exit_code": 0},
        {"exit_code": 1},
        {"error": "failed"},
        {"revision": "different"},
    ):
        a.append(
            "main", "tool_result", json.dumps({"output": "Diagnostic report", **extra})
        )
    assert len(a.search("main", "Diagnostic report")) == 4
    a.close()


def test_review_and_scaffolding_remain_exactly_zoomable(tmp_path):
    a = Archive(tmp_path / "archive.sqlite")
    a.register("main", str(tmp_path))
    review = json.dumps(
        {
            "risk_level": "low",
            "user_authorization": "high",
            "outcome": "allow",
            "rationale": "fixture budget",
        }
    )
    rid = a.append("main", "assistant", review)
    a.append("main", "developer", "<app-context> fixture budget")
    a.append("main", "assistant", "Actual budget discussion")
    assert len(a.search("main", "budget")) == 1
    assert a.zoom("main", rid)["text"] == review
    # A v0.2 process can append old classifications while this upgrade is running.
    a.db.execute("UPDATE evidence_catalog SET kind='source' WHERE event=?", (rid,))
    a.db.commit()
    a.close()
    a = Archive(tmp_path / "archive.sqlite")
    assert len(a.search("main", "budget")) == 1
    a.close()


def test_indentation_and_literal_backslashes_are_not_duplicate_noise(tmp_path):
    a = Archive(tmp_path / "memory.sqlite")
    a.register("main", str(tmp_path))
    for value in [
        "if check:\n    fail()\nsave()",
        "if check:\n    fail()\n    save()",
        r"path=C:\new",
        "path=C: new",
    ]:
        a.append("main", "tool_result", value)
    assert len(a.search("main", "check")) == 2
    assert len(a.search("main", "path")) == 2
    a.close()


@pytest.mark.parametrize("budget", [12000, 24000, 64000])
def test_budget_is_ceiling_not_padding_and_source_order_is_stable(tmp_path, budget):
    a = Archive(tmp_path / "archive.sqlite")
    a.register("main", str(tmp_path))
    a.append("main", "user", "Use duplicate suppression for retrieval.", "native:a:1")
    for i in range(80):
        a.append(
            "main",
            "tool_result",
            json.dumps({"output": "pass " * 2500, "wall_time_seconds": i}),
            f"native:a:{i + 2}",
        )
    before = a.db.execute("SELECT id,text,raw FROM events").fetchall()
    p = build_packet(a, "main", budget, 8000, session="a", now=NOW)
    assert p.tokens < 12000 and p.recent_tokens <= 8000
    assert p.selection["recent_duplicates_grouped"] == 79
    assert "duplicate suppression" in p.text
    assert build_packet(a, "main", budget, 8000, session="a", now=NOW).sha == p.sha
    assert before == a.db.execute("SELECT id,text,raw FROM events").fetchall()
    assert Tokens().count(p.text) == p.tokens
    a.close()
