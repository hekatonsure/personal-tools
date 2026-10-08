import copy
import json
from pathlib import Path
import runpy

import pytest

from memory_tool.archive import Archive
from memory_tool.proxy_capture import Capture
from memory_tool.tree_compaction import TreeCompactor
from test_proxy import compactable_request as request_bytes
from test_summary_tree import Model


def compactable_request(provider):
    return json.loads(request_bytes(provider))


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "memory.sqlite"
    archive = Archive(path)
    archive.register("main", str(tmp_path))
    archive.close()
    return path


def wire(value):
    return json.dumps(value, ensure_ascii=False, indent=2).encode()


def capture(db, value, provider):
    body = wire(value)
    saved = Capture(db, "main", "session", provider, body)
    assert not saved.input_failed
    return body, saved.input_events


def compactor(db, **kwargs):
    return TreeCompactor(
        db, "main", "session", threshold=14000, keep_turns=1, max_lines=3, **kwargs
    )


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_tree_replaces_closed_history_preserves_initial_recent_and_top_level_bytes(
    db, provider
):
    value = compactable_request(provider)
    key = "messages" if provider == "anthropic" else "input"
    # Opaque reasoning is permitted in the untouched recent suffix.
    if provider == "anthropic":
        value[key][-2]["content"].insert(
            0, {"type": "thinking", "thinking": "PRIVATE", "signature": "SIG"}
        )
    else:
        value[key].insert(-2, {"type": "reasoning", "encrypted_content": "PRIVATE"})
    body, events = capture(db, value, provider)
    model = Model()
    tree = compactor(db, summarizer=model)
    result, detail = tree.rewrite(body, provider, events)
    assert detail["applied"] and detail["summary_nodes"] == 3
    assert detail["estimated_after"] < detail["estimated_before"]
    parsed = json.loads(result)
    assert parsed[key][0] == value[key][0]
    assert "SUMMARY TREE" in parsed[key][1]["content"]
    assert body[body.index(b"task 2") :] == result[result.index(b"task 2") :]
    assert body[: body.index(b"task 0")] == result[: result.index(b"task 0")]
    assert b"PRIVATE" in result
    assert all("PRIVATE" not in text for text, _, _ in model.calls)
    archive = Archive(db)
    try:
        assert "long original output " * 1000 in "\n".join(
            r["text"] for r in archive.events("main")
        )
        assert "PRIVATE" not in "\n".join(archive.db.iterdump())
        token = parsed[key][1]["content"].split("[tree:", 1)[1].split()[0]
        page = archive.zoom("main", "tree:" + token)
        assert "Child:" in page["text"] or "Original:" in page["text"]
    finally:
        archive.close()


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_cached_prefix_survives_append_and_restart_without_model_calls(db, provider):
    value = compactable_request(provider)
    key = "messages" if provider == "anthropic" else "input"
    body, events = capture(db, value, provider)
    model = Model()
    tree = compactor(db, summarizer=model)
    first, detail = tree.rewrite(body, provider, events)
    assert detail["applied"] and detail["target_met"]
    calls = len(model.calls)
    value[key] += [
        {"role": "assistant", "content": "done"},
        {"role": "user", "content": "next"},
    ]
    second_body, events = capture(db, value, provider)
    second, detail = tree.rewrite(second_body, provider, events)
    assert detail["reason"] == "reused_prefix"
    assert json.loads(second)[key][1] == json.loads(first)[key][1]
    assert len(model.calls) == calls
    # Restart builds the same persisted view for the same original request.
    restarted = compactor(db, summarizer=model)
    original_body, original_events = capture(
        db, compactable_request(provider), provider
    )
    again, detail = restarted.rewrite(original_body, provider, original_events)
    assert again == first and len(model.calls) == calls


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
@pytest.mark.parametrize(
    "condition",
    [
        "thinking",
        "image",
        "unknown",
        "annotation",
        "unmatched",
        "cross_boundary",
        "duplicate_call",
        "missing_archive",
        "wrong_archive",
        "managed",
        "below",
        "encoded",
        "archive_failed",
        "duplicate_json",
    ],
)
def test_unsafe_spans_or_envelopes_remain_exact(db, provider, condition):
    value = compactable_request(provider)
    key = "messages" if provider == "anthropic" else "input"
    items = value[key]
    if condition in {"thinking", "image", "unknown", "annotation"}:
        block = {"type": "thinking", "thinking": "PRIVATE", "signature": "SIG"}
        if condition == "image":
            block = {"type": "image", "source": {"data": "PRIVATE"}}
        elif condition == "unknown":
            block = {"type": "new_block", "data": "PRIVATE"}
        elif condition == "annotation":
            block = {
                "type": "text",
                "text": "answer",
                "citations": [{"document": "PRIVATE"}],
            }
        if provider == "anthropic":
            items[1]["content"].insert(0, block)
        else:
            items.insert(1, {"role": "assistant", "content": [block]})
    elif condition == "unmatched":
        del items[1]
    elif condition == "cross_boundary":
        # Result at the boundary belongs to a call in the candidate span.
        items.append(copy.deepcopy(items[2]))
    elif condition == "duplicate_call":
        items.insert(2, copy.deepcopy(items[1]))
    elif condition == "managed":
        value["previous_response_id"] = "prior"
    body, events = capture(db, value, provider)
    if condition == "missing_archive":
        events = []
    elif condition == "wrong_archive":
        events = list(reversed(events))
    elif condition == "duplicate_json":
        body = body[:-1] + f', "{key}":[]}}'.encode()
    tree = compactor(db, summarizer=Model())
    if condition == "below":
        tree.threshold = 10**9
    kwargs = (
        {"encoding": "gzip"}
        if condition == "encoded"
        else {"archive_ok": False}
        if condition == "archive_failed"
        else {}
    )
    result, detail = tree.rewrite(body, provider, events, **kwargs)
    assert result == body and not detail["applied"]
    assert not tree.options["summarizer"].calls


def test_changed_history_invalidates_prefix_and_current_sources_are_used(db):
    value = compactable_request("openai")
    body, events = capture(db, value, "openai")
    tree = compactor(db)
    first, _ = tree.rewrite(body, "openai", events)
    value["input"][2]["output"] = "REVISED " * 4000
    changed_body, changed_events = capture(db, value, "openai")
    changed, detail = tree.rewrite(changed_body, "openai", changed_events)
    assert detail["applied"] and changed != first
    assert "REVISED" in changed.decode()


def test_increasing_recent_history_advances_tree_and_reports_unmet_target(db):
    value = compactable_request("openai")
    body, events = capture(db, value, "openai")
    tree = compactor(db)
    first, _ = tree.rewrite(body, "openai", events)
    # New turn makes the formerly recent call/result eligible; recent text alone
    # is bigger than the target, and must not be silently clipped.
    value["input"].append({"role": "user", "content": "NEW RECENT " * 7000})
    body, events = capture(db, value, "openai")
    second, detail = tree.rewrite(body, "openai", events)
    assert detail["applied"] and not detail["target_met"] and second != first
    assert json.loads(second)["input"][-1] == value["input"][-1]
    assert detail["tree"]["merges"] > 0


def test_reuse_rechecks_tool_pairs_when_new_suffix_references_old_call(db):
    value = compactable_request("openai")
    body, events = capture(db, value, "openai")
    tree = compactor(db)
    assert tree.rewrite(body, "openai", events)[1]["applied"]
    value["input"].append(copy.deepcopy(value["input"][2]))
    body, events = capture(db, value, "openai")
    result, detail = tree.rewrite(body, "openai", events)
    assert result == body and not detail["applied"]


def test_opaque_later_turn_preserves_suffix_but_allows_earlier_closed_span(db):
    value = compactable_request("openai")
    value["input"].insert(4, {"type": "future_opaque", "data": "PRIVATE"})
    body, events = capture(db, value, "openai")
    result, detail = compactor(db).rewrite(body, "openai", events)
    assert detail["applied"] and detail["cutoff"] == 3
    assert result[result.index(b"task 1") :] == body[body.index(b"task 1") :]


def reasoning_history():
    value = compactable_request("openai")
    for index in (7, 4, 1):
        value["input"].insert(
            index,
            {
                "type": "reasoning",
                "id": f"rs_{index}",
                "status": "completed",
                "encrypted_content": f"PRIVATE_ENCRYPTED_{index}",
                "summary": [{"type": "summary_text", "text": "PRIVATE_SUMMARY"}],
            },
        )
    return value


def test_old_reasoning_removed_recent_bytes_preserved_and_restart_reuses_tree(db):
    value = reasoning_history()
    model = Model()
    tree = compactor(db, summarizer=model)
    body, events = capture(db, value, "openai")
    first, detail = tree.rewrite(body, "openai", events)
    assert detail["applied"] and detail["reasoning_items_removed"] == 2
    assert detail["start"] == 1 and detail["cutoff"] == 8
    assert b"PRIVATE_ENCRYPTED_1" not in first
    assert b"PRIVATE_ENCRYPTED_4" not in first
    assert first[first.index(b"task 2") :] == body[body.index(b"task 2") :]
    assert all("PRIVATE" not in text for text, _, _ in model.calls)
    calls = len(model.calls)
    for instance in (tree, compactor(db, summarizer=model)):
        again, detail = instance.rewrite(body, "openai", events)
        assert again == first and len(model.calls) == calls
        assert detail["start"] == 1 and detail["cutoff"] == 8
        assert detail["reasoning_items_removed"] == 2
    archive = Archive(db)
    try:
        assert "PRIVATE" not in "\n".join(archive.db.iterdump())
    finally:
        archive.close()


@pytest.mark.parametrize(
    "change",
    [
        {"status": "in_progress"},
        {"status": "incomplete"},
        {"encrypted_content": None},
        {"encrypted_content": ""},
        {"summary": [{"type": "new_summary", "text": "PRIVATE"}]},
        {"summary": "PRIVATE"},
        {"future_field": "PRIVATE"},
        {"id": 123},
    ],
)
def test_unrecognized_or_unfinished_old_reasoning_blocks_replacement(db, change):
    value = reasoning_history()
    value["input"][1].update(change)
    body, events = capture(db, value, "openai")
    result, detail = compactor(db).rewrite(body, "openai", events)
    assert result == body and detail["reason"] == "protected_history"


@pytest.mark.parametrize("boundary", ["user", "tool_result", "end"])
def test_reasoning_without_its_model_output_blocks_replacement(db, boundary):
    value = reasoning_history()
    items = value["input"]
    if boundary == "end":
        # A dangling step immediately before the next user task.
        items.insert(4, copy.deepcopy(items[1]))
    elif boundary == "user":
        items.insert(2, {"role": "user", "content": "interrupt"})
    else:
        items[1], items[2] = items[2], items[1]
    body, events = capture(db, value, "openai")
    result, detail = compactor(db).rewrite(body, "openai", events)
    assert result == body and not detail["applied"]


def test_reasoning_with_parallel_tools_requires_all_results_inside_span(db):
    value = reasoning_history()
    items = value["input"]
    items.insert(
        3,
        {
            "type": "custom_tool_call",
            "call_id": "parallel",
            "name": "exec",
            "input": "read",
        },
    )
    items.insert(
        5, {"type": "custom_tool_call_output", "call_id": "parallel", "output": "done"}
    )
    body, events = capture(db, value, "openai")
    tree = compactor(db)
    assert tree.rewrite(body, "openai", events)[1]["applied"]
    items.append(items.pop(5))
    body, events = capture(db, value, "openai")
    result, detail = tree.rewrite(body, "openai", events)
    assert result == body and not detail["applied"]


def test_live_audit_detects_mutation_or_removal_of_retained_reasoning(db):
    audit = runpy.run_path(
        str(Path(__file__).parents[1] / "scripts" / "live_codex_session.py")
    )["retained_history_audit"]
    body, events = capture(db, reasoning_history(), "openai")
    result, detail = compactor(db).rewrite(body, "openai", events)
    checked = audit(body, result, detail)
    assert checked == {
        "retained_history_bytes_preserved": True,
        "retained_reasoning_preserved": True,
        "reasoning_items_removed": 2,
    }
    changed = result.replace(b"PRIVATE_ENCRYPTED_7", b"TAMPERED")
    assert not audit(body, changed, detail)["retained_reasoning_preserved"]
    assert not audit(body, changed, detail)["retained_history_bytes_preserved"]
    parsed = json.loads(result)
    parsed["input"] = [i for i in parsed["input"] if i.get("type") != "reasoning"]
    assert not audit(body, wire(parsed), detail)["retained_reasoning_preserved"]


def test_cached_reasoning_cutoff_must_still_be_a_user_boundary(db):
    value = reasoning_history()
    tree = compactor(db)
    body, events = capture(db, value, "openai")
    _, first = tree.rewrite(body, "openai", events)
    assert first["cutoff"] == 8
    # A changed suffix must not turn a cached task boundary into a model step.
    value["input"][8] = {"role": "assistant", "content": "continued step"}
    value["input"].append({"role": "user", "content": "task 3"})
    body, events = capture(db, value, "openai")
    result, detail = tree.rewrite(body, "openai", events)
    assert detail["applied"] and detail["cutoff"] == 12
    assert result[result.index(b"task 3") :] == body[body.index(b"task 3") :]


def test_tree_storage_failure_returns_original(db, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("PRIVATE ERROR")

    monkeypatch.setattr("memory_tool.tree_compaction.SummaryTree.advance", fail)
    body, events = capture(db, compactable_request("openai"), "openai")
    result, detail = compactor(db).rewrite(body, "openai", events)
    assert result == body and detail["reason"] == "rewrite_failed"
    assert "PRIVATE" not in json.dumps(detail)


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_local_http_proxy_tree_mode_forwards_rewrite_and_archives_original(
    db, provider
):
    from test_proxy import upstream, running, proxy, post, completed
    import hashlib

    response = (
        b'{"type":"message","role":"assistant","content":[{"type":"text","text":"done"}]}'
        if provider == "anthropic"
        else b'{"status":"completed","output":[]}'
    )
    server, received = upstream(response)
    original = (
        wire(reasoning_history()) if provider == "openai" else request_bytes(provider)
    )
    with (
        running(server) as origin,
        running(
            proxy(
                origin,
                db,
                provider,
                compact_at=14000,
                keep_turns=1,
                summary_tree=True,
                summary_lines=3,
            )
        ) as url,
    ):
        result = post(
            url,
            original,
            path="/v1/messages" if provider == "anthropic" else "/v1/responses",
        )
        assert result.content == response
        events, operations, _ = completed(db)
    forwarded = received[0][2]
    assert forwarded != original and b"MEMORY-TOOL SUMMARY TREE" in forwarded
    assert len(forwarded) < len(original)
    detail = operations[0]["detail"]
    assert detail["compaction"]["mode"] == "summary-tree"
    assert detail["compaction"]["applied"]
    assert detail["request_sha256"] == hashlib.sha256(original).hexdigest()
    assert detail["forwarded_request_sha256"] == hashlib.sha256(forwarded).hexdigest()
    assert "long original output " * 1000 in "\n".join(e["text"] for e in events)
    if provider == "openai":
        assert detail["compaction"]["reasoning_items_removed"] == 2
        assert b"PRIVATE_ENCRYPTED_7" in forwarded
        assert b"PRIVATE_ENCRYPTED_1" not in forwarded


def test_http_signed_tree_request_and_count_tokens_make_no_nodes(db):
    from test_proxy import upstream, running, proxy, post, completed

    server, received = upstream(b"{}")
    original = request_bytes("anthropic")
    with (
        running(server) as origin,
        running(
            proxy(origin, db, compact_at=14000, keep_turns=1, summary_tree=True)
        ) as url,
    ):
        assert post(url, original, Digest="fixture").status_code == 200
        assert post(url, original, path="/v1/messages/count_tokens").status_code == 200
        _, operations, _ = completed(db)
    assert all(request[2] == original for request in received)
    assert operations[0]["detail"]["compaction"]["reason"] == "signed_request"
    archive = Archive(db)
    try:
        assert (
            archive.db.execute("SELECT count(*) FROM summary_nodes").fetchone()[0] == 0
        )
    finally:
        archive.close()


@pytest.mark.parametrize("phase", ["final_answer", "commentary", "unknown_phase"])
def test_codex_public_phase_is_supported_but_unknown_phase_is_protected(db, phase):
    value = compactable_request("openai")
    value["input"].insert(
        3,
        {
            "type": "message",
            "role": "assistant",
            "phase": phase,
            "content": [{"type": "output_text", "text": "LOADED"}],
        },
    )
    body, events = capture(db, value, "openai")
    result, detail = compactor(db).rewrite(body, "openai", events)
    if phase == "unknown_phase":
        assert result == body and not detail["applied"]
    else:
        assert result != body and detail["applied"]
        assert "SUMMARY TREE" in result.decode()
