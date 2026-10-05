import json
import re

from memory_tool.archive import Archive
from memory_tool.labels import label_events
from memory_tool.packing import build_packet
from memory_tool.retrieval import search
from memory_tool.selection import duplicate_key, timestamp

NOW = timestamp("2026-10-04T22:00:00Z")


def image_result(data):
    return json.dumps(
        [
            {"type": "input_text", "text": "Script completed"},
            {"type": "input_image", "image_url": f"data:image/jpeg;base64,{data}"},
        ]
    )


def test_harness_injections_are_scaffolding_but_zoomable(tmp_path):
    a = Archive(tmp_path / "a.sqlite")
    a.register("main", str(tmp_path))
    injected = [
        a.append("main", "user", "# AGENTS.md instructions\n\n<INSTRUCTIONS>take_note"),
        a.append("main", "user", "<skill>\n<name>catchup</name>"),
        a.append("main", "user", "<environment_context>\n  <cwd>/x</cwd>"),
        a.append("main", "developer", "<turn_aborted>\nThe previous turn"),
        a.append("main", "user", "[connectome memory checkpoint — not a user message]"),
    ]
    kept = a.append("main", "user", "<send_user_message_question_reply>keep A")
    assert [e["id"] for e in a.event_index("main")] == [kept]
    assert a.zoom("main", injected[0])["text"].startswith("# AGENTS.md")
    a.close()


def test_images_become_digests_that_keep_distinct_images_apart():
    row = lambda data: {"role": "tool_result", "text": image_result(data)}
    assert duplicate_key(row("AAAA")) == duplicate_key(row("AAAA"))
    assert duplicate_key(row("AAAA")) != duplicate_key(row("BBBB"))


def test_older_history_groups_replayed_copies(tmp_path):
    a = Archive(tmp_path / "a.sqlite")
    a.register("main", str(tmp_path))
    # A resumed session replays the same timestamped turns under a new session id.
    for session in ("a", "b", "c"):
        for i in range(40):
            a.append(
                "main",
                "assistant",
                f"plan step {i} widget",
                f"native:{session}:{i}",
                ts=f"2026-10-01T00:00:{i:02d}Z",
            )
    a.append("main", "tool_result", image_result("Q" * 50000), "native:c:99")
    p = build_packet(a, "main", 24000, 2000, now=NOW)
    grouped = (
        p.selection["older_duplicates_grouped"]
        + p.selection["recent_duplicates_grouped"]
    )
    assert grouped == 80
    for i in range(40):
        assert p.text.count(f"plan step {i} widget") <= 1
    assert "QQQQ" not in p.text
    a.close()


def test_focused_events_are_not_repeated_as_tree_previews(tmp_path):
    a = Archive(tmp_path / "a.sqlite")
    a.register("main", str(tmp_path))
    a.append("main", "user", "Decide the gearbox ratio for widget arm.", "native:a:0")
    for i in range(80):
        a.append("main", "assistant", f"filler note {i} " * 20, f"native:a:{i + 1}")
    p = build_packet(a, "main", 24000, 600, session="a", focus="gearbox", now=NOW)
    assert p.text.count("gearbox ratio") == 1
    a.close()


def test_catalog_upgrade_from_kind_only_schema(tmp_path):
    a = Archive(tmp_path / "a.sqlite")
    a.register("main", str(tmp_path))
    event = a.append("main", "user", "kept decision")
    with a.db:
        a.db.execute("DROP TABLE evidence_catalog")
        a.db.execute(
            "CREATE TABLE evidence_catalog(event INTEGER PRIMARY KEY,kind TEXT NOT NULL)"
        )
        a.db.execute("UPDATE derived_versions SET version=3 WHERE name='catalog'")
    a.close()
    reopened = Archive(tmp_path / "a.sqlite")
    assert [r["id"] for r in reopened.event_index("main")] == [event]
    assert reopened.search("main", "decision")[0]["event"] == event
    reopened.close()


class ScoringClient:
    """Fake Jev endpoint: scores each request's single passage by a keyword."""

    def __init__(self):
        self.bodies = []

    def post(self, url, json, **kwargs):
        self.bodies.append(json)
        score = 3 if "approved" in json["state"]["passage"]["text"] else 0.4

        class Response:
            status_code = 200

            def json(self):
                return {"answers": {"q0": {"score": score}}}

        return Response()


class LabelClient:
    """Fake Jev endpoint: 'decided' text is a decision, everything else routine."""

    def __init__(self):
        self.bodies = []

    def post(self, url, json, **kwargs):
        self.bodies.append(json)
        decided = "decided" in json["state"]["text"]
        answers = {
            name: {"noul": (0.9 if decided else 0.05) if name != "routine" else 0.8}
            for name in json["questions"]
        }

        class Response:
            status_code = 200

            def json(self):
                return {"answers": answers, "usage": {"input_tokens": 10}}

        return Response()


def test_labels_drop_routine_older_events_and_reserve_decisions(tmp_path):
    a = Archive(tmp_path / "a.sqlite")
    a.register("main", str(tmp_path))
    decision = a.append(
        "main", "assistant", "We decided on the zeta gearbox.", "native:a:0"
    )
    for i in range(70):
        a.append("main", "tool_result", f"routine ack {i}", f"native:a:{i + 1}")
        # Replayed copy in a forked session: same document, labeled once.
        a.append("main", "tool_result", f"routine ack {i}", f"native:b:{i + 1}", ts="1")
    for i in range(30):
        a.append(
            "main", "user", f"unrelated recent chatter {i} " * 30, f"native:a:r{i}"
        )
    client = LabelClient()
    result = label_events(a, "main", key="fixture", client=client)
    assert result["labeled"] == len(client.bodies) == 101
    assert label_events(a, "main", key="fixture", client=client)["labeled"] == 0
    p = build_packet(a, "main", 24000, 2000, session="a", focus="weather", now=NOW)
    assert p.selection["older_low_value_omitted"] >= 70
    assert "routine ack" not in p.text
    assert f'"event": {decision}' in p.text and "zeta gearbox" in p.text
    a.close()


def test_jev_scores_one_passage_per_request_and_drops_weak(tmp_path):
    a = Archive(tmp_path / "a.sqlite")
    a.register("main", str(tmp_path))
    strong = a.append("main", "user", "budget decision: approved at 40 dollars")
    weak = a.append("main", "assistant", "budget spreadsheet opened")
    client = ScoringClient()
    result = search(a, "main", "budget", use_jev=True, key="fixture", client=client)
    assert len(client.bodies) == 2
    assert all("passages" not in b["state"] for b in client.bodies)
    assert [h["event"] for h in result["hits"]] == [strong]
    assert [w["event"] for w in result["below_threshold"]] == [weak]
    a.close()


def test_agent_feedback_drops_noise_keeps_useful_and_annotates_search(tmp_path):
    a = Archive(tmp_path / "a.sqlite")
    a.register("main", str(tmp_path))
    noisy, copy = [
        a.append(
            "main", "assistant", "obsolete widget plan alpha", f"native:{s}:0", ts="1"
        )
        for s in "ab"
    ]
    kept = a.append("main", "tool_result", "widget torque limit is 4 Nm", "native:a:1")
    for i in range(40):
        a.append("main", "user", f"later chatter {i} " * 40, f"native:a:{i + 2}")
    a.add_feedback("main", "noise", "superseded plan", noisy)
    a.add_feedback("main", "useful", "", kept)
    a.add_feedback("main", "missing", "packet lacked the motor model")
    p = build_packet(a, "main", 24000, 1000, focus="weather", now=NOW)
    assert "obsolete widget plan" not in p.text
    assert p.selection["older_feedback_omitted"] == 1
    assert '"agent_feedback": "useful:"' in p.text and "4 Nm" in p.text
    hits = {h["event"]: h for h in search(a, "main", "obsolete widget")["hits"]}
    assert hits[noisy]["agent_feedback"] == "noise:superseded plan"
    assert hits[noisy]["duplicate_count"] == 1 and copy not in hits
    summary = a.feedback_summary("main")
    assert summary["verdicts"] == {"missing": 1, "noise": 1, "useful": 1}
    assert summary["recent_missing"] == ["packet lacked the motor model"]
    a.close()


def test_feedback_validation():
    import pytest

    a = Archive(":memory:")
    a.register("main", "/tmp")
    with pytest.raises(AssertionError):
        a.add_feedback("main", "great", "", None)
    with pytest.raises(AssertionError):
        a.add_feedback("main", "missing", " ")
    with pytest.raises(ValueError):
        a.add_feedback("main", "noise", "", 999)


def test_tree_widens_to_fill_budget(tmp_path):
    a = Archive(tmp_path / "a.sqlite")
    a.register("main", str(tmp_path))
    for i in range(256):
        a.append("main", "assistant", f"distinct finding {i}", f"native:a:{i}")
    small = build_packet(a, "main", 2400, 300, focus="weather", now=NOW)
    large = build_packet(a, "main", 24000, 300, focus="weather", now=NOW)
    shown = lambda p: len(set(re.findall(r"distinct finding (\d+)", p.text)))
    assert shown(large) > 2 * shown(small) and large.tokens <= 24000
    a.close()
