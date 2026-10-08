import json

import pytest

from memory_tool.archive import Archive
from memory_tool.knowledge import label_key, note_tree, save_note, sync_markdown
from memory_tool.labels import label_events
from memory_tool.mcp import call
from memory_tool.packing import Tokens, build_packet
from memory_tool.retrieval import search, typesafe_key
from memory_tool.selection import LABEL_VERSION, importance


@pytest.fixture
def archive(tmp_path):
    a = Archive(tmp_path / "memory.sqlite")
    a.register("main", str(tmp_path))
    yield a
    a.close()


def document(tmp_path, text):
    directory = tmp_path / "md_archive"
    directory.mkdir(exist_ok=True)
    path = directory / "decisions.md"
    path.write_text(text)
    return path


def test_markdown_revision_search_zoom_and_old_tree_survive(archive, tmp_path):
    path = document(
        tmp_path,
        "# Reason\nUse WAL for concurrent readers.\n\n## Limit\nExisting daemons keep old code.\n",
    )
    assert sync_markdown(archive, "main")["new_notes"] == 2
    first = archive.active_notes("main")
    root = note_tree(archive, "main")
    result = search(archive, "main", "concurrent readers")
    hit = result["hits"][0]
    source = json.loads(archive.zoom("main", hit["event"])["text"])
    assert (
        source["text"]
        == path.read_text()[source["source_offset"] :][: len(source["text"])]
    )
    assert source["source_line"] == 1
    assert hit["source"]["document"] == str(path)
    assert sync_markdown(archive, "main")["new_notes"] == 0
    path.write_text("# Reason\nUse a dedicated writer queue.\n")
    sync_markdown(archive, "main")
    assert len(archive.active_notes("main")) == 1
    assert not search(archive, "main", "concurrent readers")["hits"]
    assert "writer queue" in search(archive, "main", "writer queue")["hits"][0]["text"]
    assert archive.zoom("main", root)["role"] == "derived_note_tree"
    assert (
        json.loads(archive.zoom("main", "note:" + first[0]["id"])["text"])["revision"]
        != archive.active_notes("main")[0]["revision"]
    )
    # Reverting the source reuses its immutable snapshots, not duplicate records.
    path.write_text(
        "# Reason\nUse WAL for concurrent readers.\n\n## Limit\nExisting daemons keep old code.\n"
    )
    sync_markdown(archive, "main")
    assert {n["id"] for n in archive.active_notes("main")} == {n["id"] for n in first}


def test_notes_survive_sessions_explicit_corrections_and_scope(archive, tmp_path):
    event = archive.append("main", "user", "Use FIFO because order matters.")
    old = call(
        archive,
        "main",
        "memory_note",
        {
            "text": "Use FIFO because order matters.",
            "sources": [event],
            "session": "old",
            "memory_kind": "decision",
        },
    )
    packet = build_packet(archive, "main", 3000, 300, session="new", focus="FIFO")
    assert old["event"] in packet.text
    assert "note_tree" in packet.text
    new = save_note(
        archive,
        "main",
        "Replace FIFO with a priority queue because deadlines dominate.",
        sources=[old["event"]],
        supersedes=[old["record"]["id"]],
    )
    assert [n["id"] for n in archive.active_notes("main")] == [new["record"]["id"]]
    assert "FIFO" in archive.zoom("main", old["event"])["text"]
    archive.register("other", str(tmp_path / "other"))
    assert not search(archive, "other", "FIFO")["hits"]
    for ref in (old["event"], note_tree(archive, "main")):
        with pytest.raises(ValueError, match="belong"):
            archive.zoom("other", ref)
    with pytest.raises(ValueError, match="belong"):
        save_note(archive, "other", "Copy", sources=[old["event"]])


def test_independent_contradictions_not_implicitly_resolved(archive):
    save_note(archive, "main", "Chose FIFO queue.")
    save_note(archive, "main", "Chose LIFO queue.")
    assert len(archive.active_notes("main")) == 2
    assert len(search(archive, "main", "queue")["hits"]) == 2


def test_note_feedback_and_status_expiry(archive):
    note = save_note(archive, "main", "Use WAL.")
    call(
        archive, "main", "memory_feedback", {"event": note["event"], "verdict": "noise"}
    )
    assert note["event"] not in build_packet(archive, "main", 3000, 300).text
    assert search(archive, "main", "WAL")["hits"][0]["agent_feedback"].startswith(
        "noise"
    )
    with pytest.raises(ValueError, match="expires_at"):
        save_note(archive, "main", "The worker is running", memory_kind="status")
    old = save_note(
        archive,
        "main",
        "Worker running on port 87",
        memory_kind="status",
        expires_at="2020-01-01",
    )
    assert old["event"] not in build_packet(archive, "main", 3000, 300).text
    assert archive.zoom("main", old["event"])


def test_import_limits_symlinks_and_source_isolation(archive, tmp_path):
    path = document(tmp_path, "# Durable\nUse WAL.\n")
    (path.parent / "large.md").write_text("a" * (256 * 1024 + 1))
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    (outside / "secret.md").write_text("outside marker")
    (path.parent / "link.md").symlink_to(outside / "secret.md")
    with pytest.raises(ValueError, match="inside"):
        sync_markdown(archive, "main", outside)
    result = sync_markdown(archive, "main")
    assert result["skipped"] == 2
    assert len(archive.active_notes("main")) == 1
    assert not search(archive, "main", "outside marker")["hits"]


def test_legacy_unscoped_notes_require_explicit_import_and_keep_provenance(
    archive, tmp_path
):
    path = tmp_path / "notes.jsonl"
    path.write_text(
        json.dumps(
            {"ts": "2026-10-01", "type": "fact", "text": "A persistent causal finding."}
        )
        + "\n"
    )
    sync_markdown(archive, "main")
    assert archive.active_notes("main") == []
    sync_markdown(archive, "main", path)
    first = archive.active_notes("main")
    assert len(first) == 1 and first[0]["legacy_source"] == str(path)
    with path.open("a") as f:
        f.write(json.dumps({"text": "Another finding.", "ts": "2026-10-02"}) + "\n")
    sync_markdown(archive, "main")
    assert len(archive.active_notes("main")) == 2
    assert (
        search(archive, "main", "causal")["hits"][0]["event"]
        == "note:" + first[0]["id"]
    )
    archive.register("other", str(tmp_path / "other"))
    assert archive.active_notes("other") == []


def test_deleted_document_leaves_exact_note_and_tree_available(archive, tmp_path):
    path = document(tmp_path, "# Queue\nUse FIFO.\n")
    sync_markdown(archive, "main")
    note = archive.active_notes("main")[0]
    root = note_tree(archive, "main")
    path.unlink()
    sync_markdown(archive, "main")
    assert archive.active_notes("main") == []
    assert not search(archive, "main", "FIFO")["hits"]
    assert archive.zoom("main", "note:" + note["id"])
    assert archive.zoom("main", root)


class JevClient:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def post(self, url, **kwargs):
        if self.fail:
            raise RuntimeError("fixture unavailable")
        body = kwargs["json"]
        self.calls.append(body)
        if "q0" in body["questions"]:
            text = body["state"]["passage"]["text"]
            answers = {"q0": {"score": 3 if "WAL" in text else 0}}
        else:
            text = body["state"]["text"]
            answers = {
                k: {
                    "noul": (0.95 if "WAL" in text else 0.05)
                    if k in {"decision", "durable", "user_constraint"}
                    else (0.95 if "checking" in text else 0.05)
                }
                for k in body["questions"]
            }

        class Response:
            status_code = 200

            def json(self):
                return {"answers": answers, "usage": {"input_tokens": 10}}

        return Response()


def test_jev_tree_finds_paraphrase_without_lexical_hit_and_caches(archive):
    expected = save_note(archive, "main", "WAL lets readers proceed during writes.")
    for i in range(8):
        save_note(archive, "main", f"Use cyan paint for panel {i}.")
    query = "How do we avoid database contention?"
    assert search(archive, "main", query)["hits"] == []
    client = JevClient()
    result = search(archive, "main", query, use_jev=True, key="fixture", client=client)
    assert [h["event"] for h in result["hits"]] == [expected["event"]]
    assert 0 < result["note_navigation"]["judged"] <= 16
    assert len(client.calls) <= 28
    count = len(client.calls)
    cached = search(archive, "main", query, use_jev=True, key="fixture", client=client)
    assert len(client.calls) == count and cached["usage"]["cached"] > 0
    assert Tokens().count(json.dumps(result, ensure_ascii=False)) <= 3500


def test_jev_failure_keeps_lexical_note_evidence(archive):
    expected = save_note(archive, "main", "Use WAL.")
    result = search(
        archive, "main", "WAL", use_jev=True, key="fixture", client=JevClient(fail=True)
    )
    assert result["mode"] == "local_fallback"
    assert result["hits"][0]["event"] == expected["event"]


def test_jev_labels_promote_lasting_notes_not_routine_plans(archive):
    durable = save_note(archive, "main", "Use WAL because readers must not block.")
    routine = save_note(archive, "main", "I am checking the latest test output.")
    client = JevClient()
    assert label_events(archive, "main", key="fixture", client=client)["labeled"] == 2
    assert label_events(archive, "main", key="fixture", client=client)["labeled"] == 0
    packet = build_packet(archive, "main", 3000, 300, session="new")
    assert durable["event"] in packet.text
    assert routine["event"] not in packet.text
    assert packet.selection["notes_omitted"]["low_value"] == 1
    assert importance({"labels": {"plan": 0.99, "routine": 0.99}}) < 0.25
    # Actual Jev response to 'I will inspect the files and run the tests next.'
    assert (
        importance(
            {
                "labels": {
                    "decision": 0.6,
                    "user_constraint": 0.13,
                    "plan": 0.1,
                    "routine": 0.8,
                    "transient_status": 0.3,
                    "durable": 0.03,
                }
            }
        )
        < 0.25
    )
    assert (
        importance(
            {
                "labels": {
                    "outcome": 0.87,
                    "routine": 0.66,
                    "transient_status": 0.66,
                    "durable": 0.03,
                }
            }
        )
        < 0.25
    )
    assert importance({"labels": {"durable": 0.9, "routine": 0.8}}) == 0.9
    row = archive.db.execute(
        "SELECT version FROM labels WHERE dup=?", (label_key(durable["record"]),)
    ).fetchone()
    assert row[0] == LABEL_VERSION


def test_credential_fallback_accepts_only_direct_typesafe(tmp_path, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    directory = tmp_path / "jevgrep"
    directory.mkdir()
    path = directory / "credentials.json"
    path.write_text(json.dumps({"provider": "custom", "apiKey": "private-key"}))
    assert typesafe_key() is None
    path.write_text(json.dumps({"provider": "typesafe", "apiKey": "fixture-key"}))
    assert typesafe_key() == "fixture-key"
    monkeypatch.setenv("TYPESAFE_API_KEY", "override")
    assert typesafe_key() == "override"
