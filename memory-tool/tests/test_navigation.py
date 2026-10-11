import json
import importlib.util
from pathlib import Path

import pytest

from memory_tool.archive import Archive
from memory_tool.knowledge import save_note, note_tree
from memory_tool.packing import build_packet
from memory_tool.selection import select_notes
from memory_tool.navigation import keywords


def test_navigation_labels_ignore_transport_and_function_words():
    label = keywords(
        [
            "and and the and function_call name input cfe 8ade memory retrieval",
            "and memory retrieval",
        ]
    )
    assert label.startswith("memory, retrieval")
    assert "and" not in label and "cfe" not in label


def test_note_topics_reach_every_source_and_old_roots_survive(tmp_path):
    a = Archive(tmp_path / "memory.sqlite")
    a.register("main", str(tmp_path))
    refs = {
        save_note(a, "main", text, memory_kind=kind)["event"]
        for text, kind in [
            ("Use FIFO for ordered delivery.", "decision"),
            ("Investigate queue stalls.", "commitment"),
            ("Cloud rental benchmarking uses matched seeds.", "finding"),
            ("Memory retrieval must retain exact sources.", "finding"),
        ]
    }
    root = note_tree(a, "main")
    stack, found = [root], set()
    while stack:
        node = json.loads(a.zoom("main", stack.pop(), budget=8000)["text"])
        if "note" in node:
            found.add(node["note"])
        stack.extend(c["event"] for c in node["children"])
    assert found == refs
    first = next(iter(refs))
    save_note(a, "main", "Replacement finding.", supersedes=[first[5:]])
    assert root != note_tree(a, "main")
    assert a.zoom("main", root) and a.zoom("main", first)
    a.register("other", str(tmp_path / "other"))
    with pytest.raises(ValueError):
        a.zoom("other", root)
    a.close()


def test_native_packet_indexes_all_older_events_without_filling_capacity(tmp_path):
    a = Archive(tmp_path / "memory.sqlite")
    a.register("main", str(tmp_path))
    ids = []
    for i in range(150):
        ids.append(
            a.append(
                "main",
                "tool_result",
                f"old log {i}: " + "opaque bytes " * 180,
                f"native:s:{i}",
            )
        )
    a.append("main", "user", "Focus on memory retrieval.", "native:s:last")
    p = build_packet(a, "main", 24000, 1000, session="s", focus="retrieval")
    assert "MEMORY INDEX" in p.text
    assert p.tokens < 6500
    import re

    roots = re.findall(r"(?<!knowledge-)tree:([0-9a-f]{64})", p.text)
    assert roots
    stack, recovered = list(roots), set()
    while stack:
        ref = stack.pop()
        row = a.db.execute(
            "SELECT record FROM summary_nodes WHERE chat=? AND id=?", ("main", ref)
        ).fetchone()
        node = json.loads(row[0])
        if node["event"] is not None:
            recovered.add(node["event"])
        stack.extend(node["children"])
    assert ids[0] in recovered and len(recovered) > 100
    assert a.zoom("main", ids[0])["text"].startswith("old log 0:")
    assert (
        build_packet(a, "main", 64000, 1000, session="s", focus="retrieval").tokens
        < 6500
    )
    a.close()


def test_current_decisions_beat_logs_and_weak_topic_overlap():
    rows = [
        {
            "id": "old",
            "type": "pin",
            "text": "Active paidpod xyz memory setup",
            "ts": "2020-01-01",
            "session_id": "s",
        },
        {
            "id": "paper",
            "text": "Cellular memory model survey.",
            "ts": "2020-01-01",
            "scope": "project",
        },
        {
            "id": "decision",
            "text": "Use retrieval index before expanding history.",
            "memory_kind": "decision",
            "scope": "project",
            "ts": "2026-10-11",
        },
    ]
    selected, _, omitted = select_notes(rows, ["memory", "retrieval"], "s")
    assert [n["id"] for n in selected] == ["decision"]
    assert omitted["expired"] == 1 and omitted["off_topic"] == 1


def test_retire_connectome_preserves_memory_hooks_and_trust(tmp_path):
    path = Path(__file__).parents[1] / "scripts/retire_connectome.py"
    spec = importlib.util.spec_from_file_location("retire", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    own = {"command": 'memory-tool hook --connectome "C:/archive/history.sqlite"'}
    legacy = {"command": 'bun "C:/src/codex-connectome/src/hook.ts"'}
    data = {
        "hooks": {"SessionStart": [{"hooks": [legacy, own]}]},
        "trust": {"existing": True},
    }
    hooks = tmp_path / "hooks.json"
    config = tmp_path / "config.toml"
    hooks.write_text(json.dumps(data))
    config.write_text(
        '[mcp_servers.connectome_notes]\ncommand="bun"\n[mcp_servers.memory_tool]\nenabled=true\n'
    )
    assert module.retire(hooks, config)["removed_hooks"] == 1
    assert json.loads(hooks.read_text()) == data
    result = module.retire(hooks, config, True)
    assert len(result["backups"]) == 2
    assert json.loads(hooks.read_text()) == {
        "hooks": {"SessionStart": [{"hooks": [own]}]},
        "trust": {"existing": True},
    }
    assert module.retire(hooks, config, True)["changed_files"] == []
