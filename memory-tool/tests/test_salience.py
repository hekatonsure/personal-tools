import json
import subprocess

from memory_tool.archive import Archive
from memory_tool.knowledge import label_key, save_note
from memory_tool.packing import build_packet
from memory_tool.retrieval import search
from memory_tool.selection import LABEL_VERSION


def labeled_note(archive, text, general=0, **kwargs):
    saved = save_note(archive, "main", text, **kwargs)
    with archive.db:
        archive.db.execute(
            "INSERT INTO labels VALUES(?,?,?)",
            (
                label_key(saved["record"]),
                LABEL_VERSION,
                json.dumps(
                    {"durable": 1, "user_constraint": 1, "general_preference": general}
                ),
            ),
        )
    return saved["event"]


def test_unfocused_home_startup_uses_general_preferences_not_task_pins(tmp_path):
    a = Archive(tmp_path / "memory.sqlite")
    a.register("main", str(tmp_path))
    general = labeled_note(
        a, "User prefers concise PNG reports across all projects.", 0.99
    )
    robot = labeled_note(
        a,
        "For robot Z47 always raise its arms before driving.",
        memory_kind="preference",
    )
    unknown = save_note(
        a, "main", "Always park robot K92 in bay seven.", memory_kind="preference"
    )
    packet = build_packet(a, "main", 24000, 8000, session="new")
    assert general in packet.text
    assert "Z47" not in packet.text and "K92" not in packet.text
    assert packet.selection["notes_omitted"]["needs_task_context"] == 2
    assert packet.selection["unfocused_startup"]
    # All notes, even those omitted from startup, are reachable by the root.
    root = next(
        json.loads(line)["note_tree"]
        for line in packet.text.splitlines()
        if '"note_tree"' in line
    )
    pending, leaves = [root], set()
    while pending:
        node = json.loads(a.zoom("main", pending.pop(), budget=8000)["text"])
        if "note" in node:
            leaves.add(node["note"])
        pending.extend(child["event"] for child in node["children"])
    assert {general, robot, unknown["event"]} == leaves
    focused = build_packet(a, "main", 4000, 500, session="new", focus="Z47 driving")
    assert robot in focused.text and "K92" not in focused.text
    assert a.zoom("main", unknown["event"])["text"]
    a.close()


def test_repository_startup_retains_its_own_project_constraints(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    a = Archive(tmp_path / "memory.sqlite")
    a.register("main", str(tmp_path))
    specific = labeled_note(a, "In this repository, keep API handlers idempotent.")
    packet = build_packet(a, "main", 4000, 500, session="new")
    assert specific in packet.text and not packet.selection["unfocused_startup"]
    a.close()


class Judge:
    def __init__(self, fail=False):
        self.calls = 0
        self.fail = fail

    def post(self, url, **kwargs):
        self.calls += 1
        if self.fail:
            raise RuntimeError("offline fixture")
        score = 3 if "approved" in kwargs["json"]["state"]["passage"]["text"] else 0

        class Response:
            status_code = 200

            def json(self):
                return {"answers": {"q0": {"score": score}}}

        return Response()


def test_search_does_not_append_unjudged_tail_even_when_every_judgment_is_weak(
    tmp_path,
):
    a = Archive(tmp_path / "memory.sqlite")
    a.register("main", str(tmp_path))
    for i in range(20):
        a.append("main", "assistant", f"Budget unrelated progress {i}")
    judge = Judge()
    result = search(
        a, "main", "budget", use_jev=True, key="fixture", client=judge, max_ranked=4
    )
    assert result["mode"] == "jev" and not result["hits"]
    assert result["unranked_omitted"] == 16 and len(result["below_threshold"]) == 4
    assert judge.calls == 4
    cached = search(
        a, "main", "budget", use_jev=True, key="fixture", client=judge, max_ranked=4
    )
    assert cached["usage"]["cached"] == 4 and judge.calls == 4
    assert not cached["hits"]
    a.db.execute("DELETE FROM ranking_cache")
    a.db.commit()
    fallback = search(
        a, "main", "budget", use_jev=True, key="fixture", client=Judge(fail=True)
    )
    assert fallback["mode"] == "local_fallback" and fallback["hits"]
    assert fallback["unranked_omitted"] == 0
    a.close()


def test_ranked_search_hits_all_have_passing_scores(tmp_path):
    a = Archive(tmp_path / "memory.sqlite")
    a.register("main", str(tmp_path))
    for i in range(20):
        a.append("main", "user", f"Budget approved for phase {i}.")
    judge = Judge()
    result = search(
        a, "main", "budget", use_jev=True, key="fixture", client=judge, max_ranked=4
    )
    assert len(result["hits"]) == 4 and result["unranked_omitted"] == 16
    assert all(h["score"] >= 0.5 for h in result["hits"])
    assert judge.calls == 4
    a.close()
