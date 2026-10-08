import pytest

from memory_tool.archive import Archive
from memory_tool.codex import CodexGateway, compact_and_restore, record_item


class FakeRpc:
    def __init__(self):
        self.calls = []
        self.events = []
        self.thread = "fresh-1"

    def request(self, method, params):
        self.calls.append((method, params))
        if method == "thread/read":
            return {"thread": {"cwd": self.cwd, "status": {"type": "idle"}}}
        if method == "thread/start":
            return {"thread": {"id": self.thread}}
        if method == "turn/start":
            self.events += [
                {
                    "method": "item/completed",
                    "params": {
                        "threadId": self.thread,
                        "item": {
                            "type": "agentMessage",
                            "id": "answer",
                            "text": "recovered",
                            "phase": "final_answer",
                        },
                    },
                },
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": self.thread,
                        "turn": {"id": "t", "status": "completed"},
                    },
                },
            ]
            return {"turn": {"id": "t"}}
        return {}

    def next_event(self, timeout=None):
        if not self.events:
            raise TimeoutError("no completion")
        return self.events.pop(0)

    def reject(self, id):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def close(self):
        pass


@pytest.fixture
def archive(tmp_path):
    store = Archive(tmp_path / "a.sqlite")
    store.register("main", str(tmp_path))
    store.append("main", "user", "OLD_RAW_TURN_9386")
    yield store
    store.close()


def test_fresh_backend_every_turn_and_no_resume(archive):
    clients = []

    def factory():
        client = FakeRpc()
        client.thread = f"fresh-{len(clients)}"
        clients.append(client)
        return client

    gateway = CodexGateway(
        archive, "main", budget=1500, recent=500, rpc_factory=factory, mode="fresh"
    )
    assert gateway.turn("current input") == "recovered"
    assert gateway.turn("next input") == "recovered"
    assert len(clients) == 2
    for client in clients:
        assert not any(
            method in {"thread/resume", "thread/rollback"} for method, _ in client.calls
        )
        start = next(
            params for method, params in client.calls if method == "thread/start"
        )
        assert start["ephemeral"] and start["config"]["features.shell_tool"] is False
        assert {t["name"] for t in start["dynamicTools"]} == {
            "memory_search",
            "memory_zoom",
            "delegate",
        }
        inputs = next(
            params["input"] for method, params in client.calls if method == "turn/start"
        )
        assert len(inputs) == 1 and inputs[0]["text"] != "OLD_RAW_TURN_9386"
    assert archive.stats("main")["checkpoints"] == 4


def test_adaptive_reuses_then_rolls_over(archive):
    clients = []

    def factory():
        client = FakeRpc()
        client.thread = f"fresh-{len(clients)}"
        clients.append(client)
        return client

    gateway = CodexGateway(
        archive,
        "main",
        budget=1500,
        recent=500,
        rpc_factory=factory,
        reset_at=10000,
        context_limit=20000,
    )
    gateway.turn("first")
    gateway.turn("second")
    assert len(clients) == 1 and gateway.last_trace["reused"]
    gateway.estimated_tokens = 10000
    gateway.turn("third")
    assert len(clients) == 2 and not gateway.last_trace["reused"]
    gateway.close()


def test_archive_failure_never_starts_backend(archive, monkeypatch):
    def broken(*args, **kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr(archive, "append", broken)

    def must_not_start():
        raise AssertionError("Backend started before durable input")

    with pytest.raises(OSError):
        CodexGateway(archive, "main", rpc_factory=must_not_start).turn("hi")


def test_compact_ack_is_not_completion(archive):
    rpc = FakeRpc()
    rpc.cwd = archive.project("main")
    with pytest.raises(TimeoutError):
        compact_and_restore(rpc, archive, "main", "native")
    assert not any(method == "thread/inject_items" for method, _ in rpc.calls)
    assert archive.db.execute("SELECT state FROM operations").fetchone()[0] == "failed"


def test_compact_requires_completion_then_injects_exact_checkpoint(archive):
    rpc = FakeRpc()
    rpc.cwd = archive.project("main")
    rpc.events = [
        {
            "method": "item/completed",
            "params": {"threadId": "native", "item": {"type": "contextCompaction"}},
        },
        {
            "method": "turn/completed",
            "params": {"threadId": "native", "turn": {"status": "completed"}},
        },
    ]
    result = compact_and_restore(rpc, archive, "main", "native")
    assert result["restored"] and result["empty_context"] is False
    inject = next(
        params for method, params in rpc.calls if method == "thread/inject_items"
    )
    saved = archive.db.execute(
        "SELECT packet FROM checkpoints WHERE id=?", (result["checkpoint"],)
    ).fetchone()[0]
    assert inject["items"][0]["content"][0]["text"] == saved


def test_public_tool_results_and_hidden_reasoning(archive):
    record_item(
        archive, "main", "t", {"id": "r", "type": "reasoning", "content": ["hidden"]}
    )
    for number in range(2):
        item = {
            "id": str(number),
            "type": "commandExecution",
            "command": "synthetic",
            "aggregatedOutput": f"RESULT_{number}",
        }
        record_item(archive, "main", "t", item, "started")
        record_item(archive, "main", "t", item, "completed")
    assert not any("hidden" in r["text"] for r in archive.events("main"))
    assert len([r for r in archive.events("main") if r["role"] == "tool_result"]) == 2


def test_gateway_zoom_accepts_tree_references(archive):
    from memory_tool.summary_tree import SummaryTree

    event = archive.append("main", "user", "Keep this source.")
    node = SummaryTree(archive, "main", "fixture").advance([event])[0]
    gateway = CodexGateway(archive, "main")
    result = gateway._tool("memory_zoom", {"event": "tree:" + node["id"]})
    assert f"Original: memory_zoom event={event}" in result["text"]
