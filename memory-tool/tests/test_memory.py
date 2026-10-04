import json
import sqlite3

import pytest

from memory_tool.archive import Archive, canonical, digest
from memory_tool.packing import Tokens, build_packet, checkpoint


def test_encoder_is_offline_and_matches_reference(monkeypatch):
    import tiktoken
    from memory_tool.packing import offline_encoding

    offline_encoding.cache_clear()

    def forbidden(*args, **kwargs):
        raise AssertionError("Online registry loader was used")

    monkeypatch.setattr(tiktoken, "get_encoding", forbidden)
    encoded = Tokens().encoding.encode(
        "hello こんにちは🌍 שלום <|endoftext|>", disallowed_special=()
    )
    assert encoded == [
        24912,
        220,
        95839,
        64364,
        235,
        173283,
        464,
        91,
        419,
        1440,
        919,
        91,
        29,
    ]


from memory_tool.native import verify_capture


@pytest.fixture
def archive(tmp_path):
    store = Archive(tmp_path / "memory.sqlite")
    store.register("main", str(tmp_path))
    yield store
    store.close()


def test_immutable_and_restart(archive):
    event = archive.append("main", "user", "remember 8392", "same")
    assert archive.append("main", "user", "remember 8392", "same") == event
    with pytest.raises(ValueError, match="collision"):
        archive.append("main", "user", "different", "same")
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with archive.db:
            archive.db.execute("UPDATE events SET text='changed'")
    reopened = Archive(archive.path)
    assert reopened.event("main", event)["text"] == "remember 8392"
    reopened.close()


def test_scope_is_enforced(archive, tmp_path):
    archive.register("other", str(tmp_path / "other"))
    event = archive.append("other", "assistant", "foreign secret")
    assert not archive.search("main", "foreign secret")
    with pytest.raises(ValueError):
        archive.zoom("main", event)
    with pytest.raises(ValueError):
        archive.register("main", str(tmp_path / "other"))


def test_huge_unicode_zoom_is_exact_and_bounded(archive):
    original = ("こんにちは🌍 random % value שלום\n" * 1400) + "TAIL_PROOF_9371"
    event = archive.append("main", "tool_result", original)
    offset = 0
    pieces = []
    while True:
        page = archive.zoom("main", event, offset, budget=300)
        assert Tokens().count(json.dumps(page, ensure_ascii=False)) <= 300
        pieces.append(page["text"])
        if page["next_offset"] is None:
            break
        assert page["next_offset"] > offset
        offset = page["next_offset"]
    assert "".join(pieces) == original
    assert archive.search("main", "TAIL_PROOF_9371")[0]["event"] == event


@pytest.mark.parametrize("budget,recent", [(512, 100), (1500, 500), (64000, 8000)])
def test_packet_accounts_metadata_and_huge_recent(archive, budget, recent):
    archive.append("main", "user", "old decision: use CPU")
    archive.append("main", "tool_result", "x long result " * 25000)
    packet = build_packet(archive, "main", budget, recent)
    assert packet.tokens <= budget
    assert packet.recent_tokens <= recent
    assert packet.truncated_recent
    assert "memory_zoom" in packet.text
    checkpoint_id, saved = checkpoint(
        archive, "main", budget=budget, recent_budget=recent
    )
    row = archive.db.execute(
        "SELECT * FROM checkpoints WHERE id=?", (checkpoint_id,)
    ).fetchone()
    assert digest(row["packet"]) == row["sha"] == saved.sha


def test_long_history_tree_and_restart(archive):
    for index in range(10000):
        archive.append(
            "main",
            "user" if index % 9 == 0 else "tool_result",
            f"Event {index}: result " + "work " * 14,
        )
    first = build_packet(archive, "main", budget=1500, recent_budget=500)
    assert first.tokens <= 1500
    assert "literal tree excerpts" in first.text
    reopened = Archive(archive.path)
    assert (
        build_packet(reopened, "main", budget=1500, recent_budget=500).sha == first.sha
    )
    assert reopened.search("main", "9999")[0]["text"].startswith("Event 9999")
    reopened.close()


def source_fixture(tmp_path):
    path = tmp_path / "connectome.sqlite"
    db = sqlite3.connect(path)
    db.executescript(
        "CREATE TABLE sessions(id TEXT,project TEXT); CREATE TABLE events(session TEXT,seq INTEGER,ts TEXT,role TEXT,text TEXT,raw TEXT); CREATE TABLE cursors(path TEXT,session TEXT,offset INTEGER);"
    )
    db.execute("INSERT INTO sessions VALUES(?,?)", ("s", canonical(str(tmp_path))))
    for seq in range(2):
        db.execute(
            "INSERT INTO events VALUES(?,?,?,?,?,?)",
            (
                "s",
                seq,
                "2026-10-04T12:00:00Z",
                "user",
                "same semantic event",
                f"raw {seq}",
            ),
        )
    db.execute(
        "INSERT INTO events VALUES(?,?,?,?,?,?)",
        (
            "s",
            2,
            "2026-10-04T12:01:00Z",
            "developer",
            "MEMORY-TOOL/v1 generated",
            "generated raw",
        ),
    )
    db.commit()
    db.close()
    return path


def test_import_deduplicates_replay_but_preserves_provenance(archive, tmp_path):
    source = source_fixture(tmp_path)
    imported = archive.import_connectome("main", source, str(tmp_path))
    assert imported == {"new_events": 1, "new_source_occurrences": 3}
    assert len(archive.events("main")) == 1
    assert archive.stats("main")["source_occurrences"] == 3
    assert archive.import_connectome("main", source, str(tmp_path))["new_events"] == 0
    assert not archive.search("main", "generated")


def test_superseded_notes_do_not_reappear(archive):
    records = [
        {"id": "old", "type": "pin", "text": "stale"},
        {"id": "new", "type": "pin", "text": "current", "supersedes": ["old"]},
    ]
    with archive.db:
        for record in records:
            archive.db.execute(
                "INSERT INTO notes VALUES(?,?,?)",
                ("main", record["id"], json.dumps(record)),
            )
    assert [r["id"] for r in archive.active_notes("main")] == ["new"]
    assert "stale" not in build_packet(archive, "main").text


def test_capture_backlog_prevents_desktop_reset(tmp_path):
    source = source_fixture(tmp_path)
    transcript = tmp_path / "turn.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    db = sqlite3.connect(source)
    db.execute("INSERT INTO cursors VALUES(?,?,?)", (str(transcript), "s", 0))
    db.commit()
    db.close()
    with pytest.raises(ValueError, match="not current"):
        verify_capture(source, "s", str(tmp_path))
