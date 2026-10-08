import json
import sqlite3

import pytest

from memory_tool.archive import Archive


def test_search_cache_miss_after_concurrent_commit(tmp_path, monkeypatch):
    path = tmp_path / "memory.sqlite"
    a = Archive(path)
    a.register("main", str(tmp_path))
    events = [
        a.append("main", "tool_result", json.dumps({"output": f"validator {i} passed", "exit_code": 0}))
        for i in range(3)
    ]
    with a.db:
        a.db.execute("DELETE FROM tool_output_views")
    other = sqlite3.connect(path)
    original = a.tool_output

    def concurrent_write(event, text=None):
        # A proxy/hook commits after search's SELECT but before its lazy cache write.
        with other:
            other.execute("INSERT OR REPLACE INTO ranking_cache VALUES('concurrent','{}',0)")
        return original(event, text)

    monkeypatch.setattr(a, "tool_output", concurrent_write)
    try:
        assert {r["event"] for r in a.search("main", "validator")} == set(events)
        assert not a.db.in_transaction
        assert a.add_feedback("main", "useful", event=events[0])["recorded"] == "useful"
        assert other.execute("SELECT count(*) FROM tool_output_views").fetchone()[0] == 3
    finally:
        a.close()
        other.close()


def test_failed_lazy_cache_write_releases_transaction(tmp_path):
    a = Archive(tmp_path / "memory.sqlite")
    a.register("main", str(tmp_path))
    event = a.append("main", "tool_result", "validator passed")
    with a.db:
        a.db.execute("DELETE FROM tool_output_views")
        a.db.execute("CREATE TRIGGER fail_cache BEFORE INSERT ON tool_output_views BEGIN SELECT RAISE(ABORT,'cache failure'); END")
    try:
        with pytest.raises(sqlite3.IntegrityError, match="cache failure"):
            a.tool_output(event)
        assert not a.db.in_transaction
    finally:
        a.close()
