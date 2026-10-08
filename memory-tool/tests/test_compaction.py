import copy
import json

import pytest

from memory_tool.compaction import Compactor, patch_strings
from memory_tool.archive import Archive
from memory_tool.proxy_capture import Capture


def request(provider="anthropic", turns=4, length=6000):
    items = []
    sources = {}
    for i in range(turns):
        identity = f"call-{i}"
        sources[identity] = i + 10
        text = f"START-{i}:" + "value / Ω \\\n" * length + f":END-{i}"
        text = text[: length - 10] + f":END-{i}"
        items.append({"role": "user", "content": f"TASK-{i}"})
        if provider == "anthropic":
            items.extend(
                [
                    {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "thinking",
                                "thinking": "PRIVATE_THOUGHT",
                                "signature": "PRIVATE_SIGNATURE",
                            },
                            {
                                "type": "tool_use",
                                "id": identity,
                                "name": "Read",
                                "input": {
                                    "file_path": "/example",
                                    "exact": "arguments",
                                },
                            },
                            {"type": "text", "text": "Assistant words stay exact."},
                        ],
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": identity,
                                "is_error": i == 0,
                                "content": text,
                            }
                        ],
                    },
                ]
            )
        else:
            items.extend(
                [
                    {
                        "type": "reasoning",
                        "id": f"r{i}",
                        "encrypted_content": "PRIVATE_ENCRYPTED",
                    },
                    {
                        "type": "function_call",
                        "id": f"f{i}",
                        "call_id": identity,
                        "name": "Read",
                        "arguments": '{"file_path":"/example"}',
                    },
                    {
                        "type": "function_call_output",
                        "call_id": identity,
                        "output": text,
                    },
                    {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "Assistant words stay exact.",
                            }
                        ],
                    },
                ]
            )
    key = "messages" if provider == "anthropic" else "input"
    return {
        "model": "fixture",
        "system" if provider == "anthropic" else "instructions": "SYSTEM stays exact.",
        key: items,
        "tools": [{"name": "Read", "description": "Schema stays exact"}],
    }, sources


def wire(value):
    return json.dumps(value, ensure_ascii=False, indent=2).encode()


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_only_archived_old_results_change_and_recent_reasoning_is_exact(provider):
    value, sources = request(provider)
    original = wire(value)
    compactor = Compactor(threshold=3000, keep_turns=1, result_chars=200)
    result, detail = compactor.rewrite(original, provider, sources)
    assert detail["applied"] and detail["estimated_after"] < detail["estimated_before"]
    parsed = json.loads(result)
    key = "messages" if provider == "anthropic" else "input"
    assert {k: v for k, v in parsed.items() if k != key} == {
        k: v for k, v in value.items() if k != key
    }
    changes = 0
    for before, after in zip(value[key], parsed[key], strict=True):
        if before != after:
            changes += 1
            old = before["content"][0] if provider == "anthropic" else before
            new = after["content"][0] if provider == "anthropic" else after
            field = "content" if provider == "anthropic" else "output"
            assert {k: v for k, v in new.items() if k != field} == {
                k: v for k, v in old.items() if k != field
            }
            assert "memory_zoom event=" in new[field] and new[field].endswith(
                old[field][-10:]
            )
    assert changes == 3
    # Everything from the latest task onward, including whitespace and escaped
    # strings, is byte-for-byte unchanged, not merely equivalent JSON.
    assert result[result.index(b"TASK-3") :] == original[original.index(b"TASK-3") :]
    assert (
        b"PRIVATE_SIGNATURE" in result
        if provider == "anthropic"
        else b"PRIVATE_ENCRYPTED" in result
    )


def test_raw_string_patching_keeps_unrelated_equal_values_and_whitespace():
    source = ' { "same" : "Ω\\n\\"x", "nested": [{"v":"Ω\\n\\"x"}], "number":1e+2 } '
    rewritten = patch_strings(source, {("nested", 0, "v"): "short"})
    assert rewritten == source.replace('"v":"Ω\\n\\"x"', '"v":"short"')


@pytest.mark.parametrize(
    "condition",
    [
        "below",
        "archive",
        "encoded",
        "incremental",
        "conversation",
        "managed",
        "few_turns",
        "missing_sources",
        "malformed",
        "duplicate_keys",
    ],
)
def test_unsupported_or_unsafe_requests_pass_byte_for_byte(condition):
    value, sources = request("openai")
    kwargs = {}
    threshold = 3000
    if condition == "below":
        threshold = 100000
    elif condition == "archive":
        kwargs["archive_ok"] = False
    elif condition == "encoded":
        kwargs["encoding"] = "gzip"
    elif condition == "incremental":
        value["previous_response_id"] = "resp-1"
    elif condition == "conversation":
        value["conversation"] = "conv-1"
    elif condition == "managed":
        value["context_management"] = [{"type": "compaction"}]
    elif condition == "few_turns":
        value, sources = request("openai", turns=1)
    elif condition == "missing_sources":
        sources = {}
    raw = wire(value)
    if condition == "malformed":
        raw = raw[:-5]
    elif condition == "duplicate_keys":
        raw = raw[:-1] + b', "input": []}'
    result, detail = Compactor(threshold, 1, 200).rewrite(
        raw, "openai", sources, **kwargs
    )
    assert result == raw and not detail["applied"]


def test_reuses_prefix_until_reduced_request_crosses_threshold():
    value, sources = request(length=6000)
    compactor = Compactor(5000, 1, 200)
    first, first_detail = compactor.rewrite(wire(value), "anthropic", sources)
    assert first_detail["applied"] and first_detail["target_met"]
    value["messages"].append({"role": "user", "content": "NEW TASK"})
    second, detail = compactor.rewrite(wire(value), "anthropic", sources)
    assert (
        detail["reason"] == "reused_prefix"
        and detail["cutoff"] == first_detail["cutoff"]
    )
    assert json.loads(second)["messages"][:-1] == json.loads(first)["messages"]
    assert second.split(b"TASK-3")[0] == first.split(b"TASK-3")[0]
    extra, extra_sources = request(turns=1, length=18000)
    extra["messages"][1]["content"][1]["id"] = "extra-call"
    extra["messages"][2]["content"][0]["tool_use_id"] = "extra-call"
    value["messages"].extend(extra["messages"][1:])
    sources["extra-call"] = 100
    third, detail = compactor.rewrite(wire(value), "anthropic", sources)
    assert detail["cutoff"] > first_detail["cutoff"]
    assert detail["reason"] == "compacted"
    # The new current turn alone exceeds the threshold; it must remain whole.
    assert detail["target_met"] is False
    assert json.loads(third)["messages"][-2:] == value["messages"][-2:]


def test_changed_prefix_does_not_reuse_cached_decision():
    value, sources = request()
    compactor = Compactor(3000, 1, 200)
    compactor.rewrite(wire(value), "anthropic", sources)
    value["messages"][0]["content"] = "REVISED TASK"
    result, detail = compactor.rewrite(wire(value), "anthropic", sources)
    assert detail["reason"] == "compacted"
    assert json.loads(result)["messages"][0]["content"] == "REVISED TASK"


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_mixed_images_and_unknown_blocks_survive_with_text_only_reduction(provider):
    value, sources = request(provider)
    if provider == "anthropic":
        result = value["messages"][2]["content"][0]
        field = "content"
    else:
        result = value["input"][3]
        field = "output"
    result[field] = [
        {"type": "text", "text": result[field]},
        {"type": "image", "source": {"type": "base64", "data": "preserve-image"}},
        {"type": "unknown", "value": "preserve-unknown"},
    ]
    original = copy.deepcopy(result)
    rewritten, _ = Compactor(3000, 1, 200).rewrite(wire(value), provider, sources)
    item = (
        json.loads(rewritten)["messages"][2]["content"][0]
        if provider == "anthropic"
        else json.loads(rewritten)["input"][3]
    )
    assert item[field][0]["text"] != original[field][0]["text"]
    assert item[field][1:] == original[field][1:]


def test_unmatched_tool_outputs_are_not_shortened():
    value, sources = request("openai")
    value["input"][1:3] = []  # Remove first call and reasoning, retain its output.
    result, _ = Compactor(3000, 1, 200).rewrite(wire(value), "openai", sources)
    assert json.loads(result)["input"][1] == value["input"][1]


def test_failed_patch_passes_original_without_committing_prefix(monkeypatch):
    value, sources = request()
    compactor = Compactor(3000, 1, 200)

    def broken(*args):
        raise ValueError("private payload must not appear in metadata")

    monkeypatch.setattr("memory_tool.compaction.patch_strings", broken)
    raw = wire(value)
    result, detail = compactor.rewrite(raw, "anthropic", sources)
    assert result == raw and detail["reason"] == "rewrite_failed"
    assert "private payload" not in json.dumps(detail)
    assert compactor.cutoff == 0 and compactor.prefix is None


def test_compaction_pointer_retrieves_full_original(tmp_path):
    path = tmp_path / "memory.sqlite"
    archive = Archive(path)
    archive.register("main", str(tmp_path))
    value, _ = request()
    capture = Capture(path, "main", "session-one", "anthropic", wire(value))
    result, detail = Compactor(3000, 1, 200).rewrite(
        wire(value), "anthropic", capture.result_sources
    )
    assert detail["applied"]
    event = capture.result_sources["call-0"]
    assert f"memory_zoom event={event}" in result.decode()
    row = archive.db.execute("SELECT text FROM events WHERE id=?", (event,)).fetchone()
    stored = json.loads(row[0])["content"][0]["text"]
    assert stored == value["messages"][2]["content"][0]["content"]
    assert "PRIVATE_" not in "\n".join(archive.db.iterdump())
    archive.close()
