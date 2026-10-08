import json

from memory_tool.archive import Archive
from memory_tool.packing import build_packet
from memory_tool.retrieval import search


def test_replayed_reviews_and_search_results_do_not_displace_original(tmp_path):
    with_store = Archive(tmp_path / "memory.sqlite")
    with_store.register("main", str(tmp_path))
    original = with_store.append(
        "main", "user", "Use the configured provider; five dollar total cap."
    )
    echo = json.dumps(
        {
            "hits": [{"event": original, "text": "five dollar total cap"}],
            "candidates": 1,
            "evidence": "historical",
        }
    )
    for i in range(60):
        with_store.append(
            "main",
            "user",
            "The following is the Codex agent history whose request action you are assessing.\n"
            + "five dollar total cap " * 100,
        )
        with_store.append("main", "tool_result", json.dumps({"text": echo}))
    result = search(with_store, "main", "What total cap did I authorize?")
    assert [hit["event"] for hit in result["hits"]] == [original]
    assert "five dollar" in with_store.zoom("main", original)["text"]
    assert len(with_store.events("main")) == 1
    assert with_store.stats("main")["events"] == 121  # nothing deleted
    assert (
        "request action you are assessing" not in build_packet(with_store, "main").text
    )
    with_store.close()


def test_overlap_dedup_keeps_distinct_sources_and_changed_facts(tmp_path):
    archive = Archive(tmp_path / "memory.sqlite")
    archive.register("main", str(tmp_path))
    long = archive.append(
        "main", "tool_result", "budget " * 20000 + "UNIQUE_FAILURE_71"
    )
    old = archive.append("main", "user", "The budget is five dollars.", ts="2026-10-03")
    new = archive.append("main", "user", "The budget is ten dollars.", ts="2026-10-04")
    rows = archive.search("main", "budget")
    ids = [r["event"] for r in rows]
    assert len(ids) == len(set(ids)) and {old, new, long} <= set(ids)
    assert archive.search("main", "UNIQUE_FAILURE_71")[0]["event"] == long
    archive.close()


def test_paid_ranking_is_bounded_after_dedup(tmp_path):
    archive = Archive(tmp_path / "memory.sqlite")
    archive.register("main", str(tmp_path))
    for index in range(24):
        archive.append("main", "user", f"Decision {index}: budget approved")

    class Client:
        calls = 0

        def post(self, url, **kwargs):
            self.calls += 1

            class Response:
                status_code = 200

                def json(self):
                    return {
                        "answers": {
                            key: {"score": 2} for key in kwargs["json"]["questions"]
                        }
                    }

            return Response()

    client = Client()
    result = search(
        archive, "main", "budget", use_jev=True, key="fixture", client=client
    )
    assert client.calls == 12 and result["ranked_candidates"] == 12
    assert result["candidates"] == 24
    archive.close()


def test_catalog_upgrade_invalidates_old_tree_excerpts(tmp_path):
    archive = Archive(tmp_path / "memory.sqlite")
    archive.register("main", str(tmp_path))
    event = archive.append(
        "main",
        "user",
        "The following is the Codex agent history whose request action you are assessing. stale-copy",
    )
    archive.db.execute("DELETE FROM evidence_catalog")
    archive.db.execute(
        "INSERT INTO nodes VALUES(?,?,?,?)",
        ("main", 0, 32, json.dumps([{"event": event, "text": "stale-copy"}])),
    )
    archive.db.commit()
    archive.close()
    reopened = Archive(tmp_path / "memory.sqlite")
    assert reopened.db.execute("SELECT count(*) FROM nodes").fetchone()[0] == 0
    assert not reopened.search("main", "stale-copy")
    reopened.close()


def test_repeated_dated_prompts_do_not_starve_source_outcomes(tmp_path):
    a = Archive(tmp_path / "memory.sqlite")
    a.register("main", str(tmp_path))
    repeated = [
        a.append(
            "main",
            "user",
            "Continue incremental summary tree validation",
            ts=str(1700000000 + i),
        )
        for i in range(850)
    ]
    outcome = a.append(
        "main",
        "assistant",
        "Summary tree validation passed: recovered omitted receipts after restart.",
    )
    before = a.db.execute("SELECT * FROM events").fetchall()
    result = search(a, "main", "incremental summary tree validation")
    assert len(result["hits"]) == 2
    assert outcome in {h["event"] for h in result["hits"]}
    grouped = next(h for h in result["hits"] if h["role"] == "user")
    assert grouped["duplicate_count"] == 849
    assert len(grouped["duplicate_sources"]) == 4
    assert len({r["ts"] for r in grouped["duplicate_sources"]}) == 4
    assert (
        a.zoom("main", repeated[-1])["text"]
        == "Continue incremental summary tree validation"
    )
    assert before == a.db.execute("SELECT * FROM events").fetchall()
    a.close()


def test_grouping_preserves_changed_tails_dates_and_independent_feedback(tmp_path):
    a = Archive(tmp_path / "memory.sqlite")
    a.register("main", str(tmp_path))
    first = a.append("main", "user", "Budget approved", ts="2026-10-01")
    second = a.append("main", "user", "Budget approved", ts="2026-10-02")
    a.add_feedback("main", "wrong", "Approval subsequently disputed", first)
    prefix = "budget " * 180
    old = a.append("main", "assistant", prefix + "five dollars")
    new = a.append("main", "assistant", prefix + "ten dollars")
    hits = {h["event"]: h for h in search(a, "main", "budget")["hits"]}
    assert {first, second, old, new} == set(hits)
    assert hits[first]["agent_feedback"].startswith("wrong:")
    assert "agent_feedback" not in hits[second]
    a.close()


def test_retrieval_requests_are_zoomable_but_not_search_evidence(tmp_path):
    a = Archive(tmp_path / "memory.sqlite")
    a.register("main", str(tmp_path))
    source = a.append("main", "assistant", "Gearbox validation passed")
    calls = [
        {
            "name": "mcp__memory_tool__memory_search",
            "arguments": {"query": "gearbox validation"},
        },
        {
            "name": "exec",
            "input": 'text(await tools.mcp__memory_tool__memory_search({query:"gearbox validation"}));',
        },
        {
            "name": "exec",
            "input": """await Promise.allSettled([
            tools.exec_command({cmd:'session-search "gearbox validation"'}),
            tools.mcp__memory_tool__memory_search({query:"gearbox validation"}),
            tools.mcp__memory_tool__memory_status({})]);""",
        },
    ]
    ids = [a.append("main", "tool_call", json.dumps(c)) for c in calls]
    assert set(ids) <= {e["id"] for e in a.events("main")}
    assert [h["event"] for h in search(a, "main", "gearbox validation")["hits"]] == [
        source
    ]
    for event, call in zip(ids, calls):
        assert json.loads(a.zoom("main", event)["text"]) == call
    # An actual operation and a mixed batch are still evidence of attempts.
    actual = a.append(
        "main",
        "tool_call",
        json.dumps(
            {
                "name": "exec",
                "input": 'await tools.exec_command({cmd:"gearbox validation"});',
            }
        ),
    )
    mixed = a.append(
        "main",
        "tool_call",
        json.dumps(
            {
                "name": "exec",
                "input": 'await tools.mcp__memory_tool__memory_search({query:"gearbox"}); await tools.apply_patch("gearbox validation");',
            }
        ),
    )
    hits = search(a, "main", "gearbox validation")["hits"]
    assert hits[0]["event"] == source
    assert {actual, mixed} <= {h["event"] for h in hits}
    a.close()


def test_worker_summaries_and_status_echoes_do_not_become_original_evidence(tmp_path):
    a = Archive(tmp_path / "memory.sqlite")
    a.register("main", str(tmp_path))
    snapshot = json.dumps(
        {
            "now": "gearbox validation",
            "facts": [],
            "hypotheses": [],
            "stance": "ready",
            "pins": [],
            "todo": [],
        }
    )
    generated = a.append("main", "assistant", snapshot)
    status = json.dumps(
        {
            "source_events": 10,
            "agent_feedback": {"recent_missing": ["gearbox validation"]},
            "native_recovery": {"state": "prepared"},
        }
    )
    echo = a.append(
        "main", "tool_result", json.dumps({"text": json.dumps({"text": status})})
    )
    original = a.append("main", "assistant", "Gearbox validation passed.")
    # Ordinary JSON/code discussing snapshots remains evidence.
    ordinary = a.append(
        "main", "assistant", json.dumps({"facts": ["gearbox validation"]})
    )
    # Opening a pre-upgrade archive must reclassify derived metadata without
    # touching source bytes or leaving the old generated passages searchable.
    before = a.db.execute("SELECT * FROM events").fetchall()
    with a.db:
        a.db.execute("UPDATE derived_versions SET version=4 WHERE name='catalog'")
        a.db.execute("UPDATE evidence_catalog SET kind='source'")
    a.close()
    a = Archive(tmp_path / "memory.sqlite")
    assert before == a.db.execute("SELECT * FROM events").fetchall()
    assert {h["event"] for h in search(a, "main", "gearbox validation")["hits"]} == {
        original,
        ordinary,
    }
    assert a.zoom("main", generated)["text"] == snapshot
    assert (
        status in json.loads(json.loads(a.zoom("main", echo)["text"])["text"])["text"]
    )
    a.close()


def test_local_decision_queries_prefer_direct_statements_but_raw_queries_keep_results(
    tmp_path,
):
    a = Archive(tmp_path / "memory.sqlite")
    a.register("main", str(tmp_path))
    tool = a.append("main", "tool_result", "gearbox decisions " * 100)
    direct = a.append("main", "user", "We approved the gearbox design.")
    assert search(a, "main", "gearbox decisions")["hits"][0]["event"] == direct
    assert search(a, "main", "gearbox")["hits"][0]["event"] == tool
    a.close()


def test_snapshot_reclassification_does_not_change_native_capture_identity(tmp_path):
    from memory_tool.archive import digest
    from memory_tool.hooks import capture_rollout

    a = Archive(tmp_path / "memory.sqlite")
    a.register("main", str(tmp_path))
    text = json.dumps(
        {
            "now": "gearbox validation",
            "facts": [],
            "hypotheses": [],
            "stance": "ready",
            "pins": [],
            "todo": [],
        }
    )
    ts = "2026-10-07T12:00:00Z"
    original = a.append(
        "main",
        "assistant",
        text,
        f"native:old-session:{ts}:assistant:{digest(text)}",
        ts=ts,
    )
    transcript = tmp_path / "rollout.jsonl"
    transcript.write_text(
        "\n".join(
            json.dumps(r)
            for r in [
                {
                    "type": "session_meta",
                    "payload": {"id": "old-session", "cwd": str(tmp_path)},
                },
                {
                    "type": "response_item",
                    "timestamp": ts,
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": text}],
                    },
                },
            ]
        )
        + "\n"
    )
    capture_rollout(
        a,
        "main",
        {
            "session_id": "old-session",
            "cwd": str(tmp_path),
            "transcript_path": str(transcript),
        },
    )
    assert a.db.execute("SELECT count(*) FROM events").fetchone()[0] == 1
    assert a.event("main", original)["role"] == "assistant"
    assert a.zoom("main", original)["text"] == text
    assert not search(a, "main", "gearbox validation")["hits"]
    a.close()
