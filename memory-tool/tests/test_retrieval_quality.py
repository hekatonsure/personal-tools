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
