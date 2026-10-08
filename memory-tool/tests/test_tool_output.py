import json

from memory_tool.archive import Archive
from memory_tool.evidence import source_kind
from memory_tool.retrieval import search
from memory_tool.tool_output import searchable_output


def wrapped(*outputs, proxy=False):
    blocks = [
        {
            "type": "input_text",
            "text": "Script completed\nWall time 0.1 seconds\nOutput:\n",
        }
    ]
    for output in outputs:
        blocks.append(
            {
                "type": "input_text",
                "text": json.dumps(
                    {
                        "i": 0,
                        "result": {
                            "status": "fulfilled",
                            "value": {
                                "exit_code": 0,
                                "chunk_id": "random",
                                "output": output,
                            },
                        },
                    }
                ),
            }
        )
    if proxy:
        return json.dumps(
            {"type": "custom_tool_call_output", "call_id": "test", "output": blocks}
        )
    return json.dumps(blocks)


def test_nested_startup_reference_removed_but_mixed_diagnostics_survive(tmp_path):
    a = Archive(tmp_path / "m.sqlite")
    a.register("main", str(tmp_path))
    reference = json.dumps(
        {
            "project": "/w",
            "session": "old",
            "topic_event": 1,
            "topic_excerpt": "obsolete gearbox decision",
            "last_reply": {"excerpt": "gearbox approved"},
        }
    )
    output = wrapped("startup probe\n" + reference, "43 passed; validator OK")
    event = a.append("main", "tool_result", output)
    assert not search(a, "main", "gearbox")["hits"]
    hit = search(a, "main", "validator")["hits"][0]
    assert hit["event"] == event and "43 passed" in hit["text"]
    assert "topic_excerpt" not in hit["text"] and '\\"' not in hit["text"]
    assert hit["presentation"] and hit["offset"] == 0
    assert a.zoom("main", event)["text"] == output
    a.close()


def test_transport_copies_group_without_collapsing_different_results(tmp_path):
    a = Archive(tmp_path / "m.sqlite")
    a.register("main", str(tmp_path))
    one = a.append("main", "tool_result", wrapped("validator passed"))
    two = a.append("main", "tool_result", wrapped("validator passed", proxy=True))
    failed = a.append("main", "tool_result", wrapped("validator failed"))
    hits = search(a, "main", "validator")["hits"]
    assert len(hits) == 2
    grouped = next(h for h in hits if h["duplicate_count"])
    assert grouped["event"] in {one, two} and grouped["duplicate_count"] == 1
    assert failed in {h["event"] for h in hits}
    a.close()


def test_echo_filter_does_not_hide_new_failures_or_code_discussing_schema():
    echo = json.dumps({"hits": [], "candidates": 0, "evidence": "history"})
    assert source_kind("tool_result", wrapped(echo)) == "retrieval_echo"
    mixed = wrapped(echo, "UNIQUE_FAILURE: motor stalled")
    assert source_kind("tool_result", mixed) == "source"
    assert "UNIQUE_FAILURE" in searchable_output(mixed)
    assert '"hits"' not in searchable_output(mixed)
    code = 'if "hits" in plain and "evidence" in plain and "candidates" in plain:\n    return "retrieval_echo"'
    assert searchable_output(wrapped(code)).endswith(code)
    assert source_kind("tool_result", wrapped(code)) == "source"
    rejected = json.dumps({"status": "rejected", "reason": "motor stalled"})
    assert "motor stalled" in searchable_output(rejected)


def test_status_unknown_payloads_and_literal_backslashes_are_preserved():
    for code in (0, 1, None):
        assert f"exit_code: {code}" in searchable_output(
            json.dumps({"output": "motor", "exit_code": code})
        )
    unknown = {"output": "motor", "exit_code": 0, "unexpected": "important"}
    assert "important" in searchable_output(json.dumps(unknown))
    literal = "C:\\new\\test literal \\n and indentation\n    preserved"
    assert searchable_output(wrapped(literal)).endswith(literal)


def test_catalog_upgrade_preserves_original_records_and_zoom(tmp_path):
    path = tmp_path / "m.sqlite"
    a = Archive(path)
    a.register("main", str(tmp_path))
    echo = wrapped(
        json.dumps(
            {
                "project": "/w",
                "session": "old",
                "topic_event": 1,
                "topic_excerpt": "old motor",
            }
        )
    )
    event = a.append("main", "tool_result", echo)
    before = list(map(tuple, a.db.execute("SELECT * FROM events")))
    with a.db:
        a.db.execute("UPDATE derived_versions SET version=5 WHERE name='catalog'")
        a.db.execute("UPDATE evidence_catalog SET kind='source'")
    a.close()
    a = Archive(path)
    assert not a.search("main", "motor")
    assert before == list(map(tuple, a.db.execute("SELECT * FROM events")))
    assert a.zoom("main", event)["text"] == echo
    a.close()


def test_printed_replay_rows_are_not_fresh_evidence():
    probe = 'QUERY old motor hits 2\n10 assistant duplicates 0 {\n  "now": "motor plan"\n11 user duplicates 0 old decision\nseconds 0.45\n'
    output = searchable_output(wrapped(probe, "new validator failed"))
    assert "motor" not in output and "old decision" not in output
    assert "seconds 0.45" in output and "new validator failed" in output
    assert (
        searchable_output("QUERY plan hits 2\nordinary source code")
        == "QUERY plan hits 2\nordinary source code"
    )


def test_session_search_json_after_progress_is_an_echo():
    results = [
        {
            "id": "old",
            "source": "codex",
            "resume": "codex resume old",
            "hits": [{"excerpt": "old motor decision"}],
        }
    ]
    output = searchable_output(
        wrapped("cards judged in 0.4s\n" + json.dumps(results, indent=2))
    )
    assert "old motor" not in output and "cards judged" in output
    assert source_kind("tool_result", json.dumps(results)) == "retrieval_echo"
    debug = (
        "EVENT 10 assistant len 20\nold motor\nEVENT 11 tool_result len 22\nold output"
    )
    assert "old motor" not in searchable_output(wrapped(debug, "new validator failed"))
    raw = '10 tool_result 200 [{"text":"old motor"}]\nmarkers {}\n11 assistant 200 {"facts":[]}'
    assert "old motor" not in searchable_output(wrapped(raw, "new validator failed"))


def test_jsonl_transport_after_warning_is_decoded():
    text = "Warning: truncated output\n" + json.dumps(
        {"chunk_id": "a", "exit_code": 1, "output": "motor failed"}
    )
    output = searchable_output(wrapped(text))
    assert "exit_code: 1" in output and "motor failed" in output
    assert "chunk_id" not in output


def test_malformed_transports_remain_visible():
    for value in (
        [{"type": "text"}],
        [{"type": "text", "text": None}],
        {"exit_code": [], "output": ""},
    ):
        output = searchable_output(json.dumps(value))
        assert output


def test_python_hit_and_query_debug_copies_preserve_fresh_failures():
    hit = {"event": 12, "offset": 0, "score": None, "text": "old gearbox"}
    replay = (
        repr(hit)
        + "\n"
        + "gearbox query [(12, 'old gearbox'), (13, 'old motor')]\n"
        + "motor query [(14, 'old motor'), (15, 'old gearbox')]\n"
        + "Traceback (most recent call last):\nAssertionError: NEW_FAILURE\n"
    )
    view = searchable_output(wrapped(replay))
    assert "old gearbox" not in view and "old motor" not in view
    assert "AssertionError: NEW_FAILURE" in view
    ordinary = "inventory [(12, 'gearbox'), (13, 'motor')]"
    assert ordinary in searchable_output(wrapped(ordinary))
    assert "useful" in searchable_output(wrapped("{'event': 12, 'text': 'useful'}"))


def test_raw_view_diagnostics_do_not_replay_old_successes():
    replay = (
        "All checks passed!\n"
        "6011 raw chars 501 view chars 196 startup refs remaining False\n"
        "exit_code: 0\nold commit bc9bcaf\n"
        "5929 raw chars 577 view chars 196 startup refs remaining False\n"
        "old commit bc9bcaf\n"
        "AssertionError: NEW_FAILURE\n"
    )
    view = searchable_output(wrapped(replay, "42 passed in 2.47s"))
    assert "bc9bcaf" not in view
    assert "All checks passed!" in view and "42 passed in 2.47s" in view
    assert "NEW_FAILURE" in view
    assert "raw chars" in searchable_output("raw chars example is documentation")


def test_packets_decode_recent_and_tree_previews_and_group_transport_copies(tmp_path):
    from memory_tool.packing import build_packet

    a = Archive(tmp_path / "m.sqlite")
    a.register("main", str(tmp_path))
    originals = []
    for i in range(48):
        raw = wrapped(f"validator result {i}: passed")
        originals.append((a.append("main", "tool_result", raw), raw))
    first = a.append("main", "tool_result", wrapped("UNIQUE_FINAL passed"))
    a.append("main", "tool_result", wrapped("UNIQUE_FINAL passed", proxy=True))
    call = a.append(
        "main",
        "tool_call",
        json.dumps({"name": "memory_search", "arguments": {"query": "old"}}),
    )
    scaffold = a.append(
        "main", "developer", "<collaboration_mode>Default</collaboration_mode>"
    )
    packet = build_packet(a, "main", budget=6000, recent_budget=700)
    assert "chunk_id" not in packet.text and "Script completed" not in packet.text
    assert packet.text.count("UNIQUE_FINAL") == 1
    assert packet.selection["recent_duplicates_grouped"] == 1
    assert packet.selection["retrieval_calls_omitted"] == 1
    assert "collaboration_mode" not in packet.text and '"name"' not in packet.text
    assert "validator result" in packet.text and "decoded tool output" in packet.text
    assert packet.tokens <= 6000
    for event, raw in originals:
        assert a.event("main", event)["text"] == raw
    assert a.zoom("main", first)["text"] == wrapped("UNIQUE_FINAL passed")
    assert a.zoom("main", call)["text"] and a.zoom("main", scaffold)["text"]
    a.close()


def test_decoded_packet_grouping_keeps_different_feedback(tmp_path):
    from memory_tool.packing import build_packet

    a = Archive(tmp_path / "m.sqlite")
    a.register("main", str(tmp_path))
    one = a.append("main", "tool_result", wrapped("validator final"))
    a.append("main", "tool_result", wrapped("validator final", proxy=True))
    a.add_feedback("main", "useful", "verified", one)
    packet = build_packet(a, "main", budget=3000, recent_budget=1500)
    assert packet.text.count("validator final") == 2
    assert "useful:verified" in packet.text
    a.close()


def test_tool_views_survive_restart_and_lazy_old_writer_backfill(tmp_path, monkeypatch):
    import memory_tool.archive as module

    path = tmp_path / "m.sqlite"
    a = Archive(path)
    a.register("main", str(tmp_path))
    event = a.append("main", "tool_result", wrapped("validator passed"))
    before = list(map(tuple, a.db.execute("SELECT * FROM events")))
    a.close()
    a = Archive(path)
    original = module.searchable_output

    def unexpected_decode(text):
        raise AssertionError("cached output was decoded again")

    monkeypatch.setattr(module, "searchable_output", unexpected_decode)
    assert a.search("main", "validator")[0]["event"] == event
    assert "validator" in a.event_index("main")[0]["preview"]
    monkeypatch.setattr(module, "searchable_output", original)
    # Older runtimes can append events without writing the new derived table.
    with a.db:
        a.db.execute("DELETE FROM tool_output_views WHERE event=?", (event,))
    assert a.search("main", "validator")[0]["event"] == event
    a.close()
    a = Archive(path)
    monkeypatch.setattr(module, "searchable_output", unexpected_decode)
    assert a.search("main", "validator")[0]["event"] == event
    assert before == list(map(tuple, a.db.execute("SELECT * FROM events")))
    a.close()


def test_tool_view_version_invalidation_and_append_rollback(tmp_path):
    a = Archive(tmp_path / "m.sqlite")
    a.register("main", str(tmp_path))
    event = a.append("main", "tool_result", wrapped("validator passed"))
    with a.db:
        a.db.execute("UPDATE tool_output_views SET version=0,text='obsolete cache'")
    assert "validator" in a.tool_output(event)
    try:
        with a.db:
            a.db.execute(
                "INSERT INTO events(chat,source,ts,role,text,raw) VALUES('main','rollback','1','tool_result','failure','')"
            )
            pending = a.db.execute(
                "SELECT id FROM events WHERE source='rollback'"
            ).fetchone()[0]
            assert a.tool_output(pending) == "failure"
            raise ValueError("rollback")
    except ValueError:
        pass
    assert not a.db.execute("SELECT id FROM events WHERE source='rollback'").fetchone()
    assert not a.db.execute(
        "SELECT event FROM tool_output_views WHERE event=?", (pending,)
    ).fetchone()
    a.close()


def test_expired_copied_status_does_not_expire_a_fresh_failure(tmp_path):
    from memory_tool.packing import build_packet

    a = Archive(tmp_path / "m.sqlite")
    a.register("main", str(tmp_path))
    echo = json.dumps(
        {
            "project": "/w",
            "session": "old",
            "topic_event": 1,
            "topic_excerpt": "currently running PID 123",
        }
    )
    a.append(
        "main",
        "tool_result",
        wrapped(echo, "UNIQUE_FAILURE: motor stalled"),
        ts="2000-01-01",
    )
    packet = build_packet(a, "main", budget=3000, recent_budget=1500)
    assert "UNIQUE_FAILURE" in packet.text
    assert "PID 123" not in packet.text
    a.close()
