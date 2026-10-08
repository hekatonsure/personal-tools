import json
import subprocess

import pytest

from memory_tool.archive import Archive
from memory_tool.mcp import call
from memory_tool.summary_tree import SummaryTree, node_record
from memory_tool.summarizer import CodexSummarizer


@pytest.fixture
def archive(tmp_path):
    value = Archive(tmp_path / "memory.sqlite")
    value.register("main", str(tmp_path))
    yield value
    value.close()


def add(archive, count, text="durable detail Ω " * 100):
    return [
        archive.append("main", "user" if i % 3 == 0 else "tool_result", f"{i}: {text}")
        for i in range(count)
    ]


class Model:
    identity = "fixture-v1"

    def __init__(
        self, answer="user: keep the chosen setting; tool_result: validation passed"
    ):
        self.calls = []
        self.answer = answer

    def __call__(self, text, limit, *, timeout):
        self.calls.append((text, limit, timeout))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def descendants(archive, node):
    if node["event"] is not None:
        return [node["event"]]
    return [
        event
        for child in node["children"]
        for event in descendants(archive, node_record(archive, "main", child))
    ]


def test_multilevel_tree_is_bounded_ordered_and_exact_sources_remain(archive):
    events = add(archive, 256)
    original = archive.events("main")
    tree = SummaryTree(archive, "main", "session", max_lines=4, node_bytes=128)
    nodes = tree.advance(events)
    assert len(nodes) == 4
    assert sum(n["count"] for n in nodes) == 256
    assert [e for n in nodes for e in descendants(archive, n)] == events
    assert max(n["count"] for n in nodes) >= 64
    records = [
        json.loads(r[0]) for r in archive.db.execute("SELECT record FROM summary_nodes")
    ]
    assert all(len(n["text"].encode()) <= 128 for n in records)
    assert archive.events("main") == original
    # A detail lost from a top-level excerpt is still recovered by recursive zoom.
    node = nodes[0]
    while node["children"]:
        page = call(archive, "main", "memory_zoom", {"event": "tree:" + node["id"]})
        assert "Child: memory_zoom event=tree:" in page["text"]
        node = node_record(archive, "main", node["children"][0])
    assert (
        f"Original: memory_zoom event={events[0]}"
        in archive.zoom("main", "tree:" + node["id"])["text"]
    )
    assert archive.zoom("main", events[0])["text"] == original[0]["text"]


def test_append_preserves_lines_until_fold_and_restart_reuses_model_nodes(archive):
    events = add(archive, 5)
    model = Model()
    tree = SummaryTree(
        archive, "main", "session", max_lines=4, summarizer=model, max_calls=20
    )
    first = tree.advance(events[:2])
    second = tree.advance(events[:3])
    assert second[:2] == first
    assert len(model.calls) == 3
    recreated = SummaryTree(
        archive, "main", "session", max_lines=4, summarizer=model, max_calls=20
    )
    assert recreated.advance(events[:3]) == second
    assert len(model.calls) == 3
    folded = recreated.advance(events)
    assert len(folded) == 4 and folded[0]["children"] == [n["id"] for n in first]
    # No splitting when the history is replayed.
    assert recreated.advance(events) == folded
    archive2 = Archive(archive.path)
    try:
        restarted = SummaryTree(
            archive2, "main", "session", max_lines=4, summarizer=model
        )
        assert restarted.advance(events) == folded
    finally:
        archive2.close()


def test_branch_rewind_reorder_and_other_session_do_not_reuse_frontier(archive):
    events = add(archive, 12)
    tree = SummaryTree(archive, "main", "one", max_lines=2)
    old = tree.advance(events)
    branch = tree.advance([events[0], events[2], events[1]])
    assert tree.detail["reset"]
    assert [e for n in branch for e in descendants(archive, n)] == [
        events[0],
        events[2],
        events[1],
    ]
    assert archive.zoom("main", "tree:" + old[0]["id"])["text"]
    assert (
        SummaryTree(archive, "main", "two", max_lines=2).advance(events[:1])[0]["count"]
        == 1
    )
    rewind = tree.advance(events[:1])
    assert tree.detail["reset"] and rewind[0]["event"] == events[0]


@pytest.mark.parametrize(
    "answer",
    [TimeoutError(), RuntimeError("PRIVATE_ERROR"), "", "two\nlines", "Ω" * 300],
)
def test_model_failure_falls_back_once_and_is_persisted(archive, answer):
    events = add(archive, 2)
    model = Model(answer)
    tree = SummaryTree(archive, "main", "session", max_lines=1, summarizer=model)
    nodes = tree.advance(events)
    calls = len(model.calls)
    assert calls == 3 and tree.detail["model_failures"] == 3
    assert nodes[0]["method"] == "literal"
    assert tree.advance(events) == nodes and len(model.calls) == calls
    assert "PRIVATE_ERROR" not in "\n".join(archive.db.iterdump())


def test_model_call_budget_short_messages_and_policy_cache_identity(archive):
    small = archive.append("main", "user", "Keep the threshold at 42.")
    events = [small, *add(archive, 5)]
    model = Model()
    tree = SummaryTree(archive, "main", "session", summarizer=model, max_calls=2)
    nodes = tree.advance(events)
    assert nodes[0]["text"] == "user: Keep the threshold at 42."
    assert len(model.calls) == 2
    assert [n["method"] for n in nodes] == [
        "literal",
        "model",
        "model",
        "literal",
        "literal",
        "literal",
    ]
    model.identity = "fixture-v2"
    revised = SummaryTree(
        archive, "main", "session", summarizer=model, max_calls=1
    ).advance(events)
    assert revised[0]["id"] != nodes[0]["id"]
    assert len(model.calls) == 3


def test_time_budget_and_oversized_input_do_not_call_model(archive, monkeypatch):
    event = archive.append("main", "user", "x" * 65000)
    model = Model()
    tree = SummaryTree(archive, "main", "session", summarizer=model)
    assert tree.advance([event])[0]["method"] == "literal"
    assert not model.calls
    events = add(archive, 2)
    times = iter([0, 31, 32])
    monkeypatch.setattr("memory_tool.summary_tree.time.monotonic", lambda: next(times))
    assert all(n["method"] == "literal" for n in tree.advance(events))
    assert not model.calls


def test_node_zoom_scope_and_bounded_pagination(archive, tmp_path):
    events = add(archive, 5)
    nodes = SummaryTree(
        archive, "main", "session", max_lines=1, node_bytes=4096
    ).advance(events)
    identity = "tree:" + nodes[0]["id"]
    pages, offset = [], 0
    while True:
        page = archive.zoom("main", identity, offset, budget=128)
        pages.append(page["text"])
        offset = page["next_offset"]
        if offset is None:
            break
    assert len(pages) > 1 and "Child:" in "".join(pages)
    archive.register("other", str(tmp_path / "other"))
    with pytest.raises(ValueError, match="does not belong"):
        archive.zoom("other", identity)
    with pytest.raises(ValueError):
        SummaryTree(archive, "other", "session").advance(events)


def test_concurrent_frontier_update_cannot_overwrite_new_state(archive):
    events = add(archive, 1)

    class Concurrent(Model):
        def __call__(self, *args, **kwargs):
            # Simulate another writer winning while the model call is outstanding.
            archive.db.execute(
                "INSERT INTO summary_views VALUES(?,?,?)",
                ("main", tree.scope, '{"events":[],"frontier":[]}'),
            )
            archive.db.commit()
            return super().__call__(*args, **kwargs)

    tree = SummaryTree(archive, "main", "session", summarizer=Concurrent())
    with pytest.raises(RuntimeError, match="Concurrent"):
        tree.advance(events)
    assert (
        json.loads(archive.db.execute("SELECT state FROM summary_views").fetchone()[0])[
            "events"
        ]
        == []
    )


def test_codex_adapter_isolated_no_shell_and_no_proxy_recursion(monkeypatch):
    from pathlib import Path

    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        Path(command[command.index("--output-last-message") + 1]).write_text(
            "user: keep 42."
        )
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:8787")
    monkeypatch.setattr("memory_tool.summarizer.subprocess.run", run)
    model = CodexSummarizer("fixture-model")
    assert model("user: Keep 42", 128, timeout=2) == "user: keep 42."
    command, kwargs = calls[0]
    assert command[:2] == ["codex", "exec"]
    assert "--ignore-user-config" in command and "--ephemeral" in command
    assert "features.hooks=false" in command and "features.shell_tool=false" in command
    assert (
        "features.multi_agent=false" in command
        and 'model_reasoning_effort="low"' in command
    )
    assert "OPENAI_BASE_URL" not in kwargs["env"]
    assert kwargs["timeout"] == 2 and not kwargs.get("shell")
    assert not Path(kwargs["cwd"]).exists()
