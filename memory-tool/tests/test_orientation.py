import json
import subprocess

from memory_tool.archive import Archive
from memory_tool.hooks import handle
from memory_tool.orientation import repository_root
from memory_tool.packing import Tokens, build_packet


def add(archive, chat, session, role, text, ts="2026-10-07T12:00:00Z"):
    return archive.append(chat, role, text, f"native:{session}:{role}:{text}", ts=ts)


def test_repo_first_then_retrievable_general_references(tmp_path):
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subdir = repo / "src"
    subdir.mkdir()
    a = Archive(tmp_path / "archive.sqlite")
    a.register("root", str(repo))
    a.register("subdir", str(subdir))
    a.register("elsewhere", str(tmp_path / "elsewhere"))
    topic = add(a, "root", "old-repo", "user", "Fix the robot watchdog")
    outcome = add(a, "root", "old-repo", "assistant", "Watchdog regression passed")
    remote = add(a, "elsewhere", "other", "user", "Spreadsheet audit")
    add(a, "elsewhere", "other", "tool_result", "UNRELATED_RAW_LOG")
    add(a, "subdir", "new", "user", "# AGENTS.md instructions\nscaffolding")
    before = a.db.execute("SELECT * FROM events").fetchall()
    restored = handle(
        a,
        {
            "cwd": str(subdir),
            "session_id": "new",
            "hook_event_name": "SessionStart",
            "source": "startup",
        },
        budget=4000,
        recent=1000,
    )
    text = restored["hookSpecificOutput"]["additionalContext"]
    assert text.index("REPOSITORY HISTORY") < text.index("RECENT SESSION REFERENCES")
    assert "Watchdog regression passed" in text and "Spreadsheet audit" in text
    assert "UNRELATED_RAW_LOG" not in text
    assert f'"project": "{repo}"' in text
    assert f'"topic_event": {topic}' in text
    assert f'"latest_event": {outcome}' in text
    assert a.zoom("elsewhere", remote)["text"] == "Spreadsheet audit"
    assert Tokens().count(text) <= 4000
    assert before == a.db.execute("SELECT * FROM events").fetchall()
    assert a.project("subdir") == str(subdir)
    a.close()


def test_home_startup_does_not_guess_repo_from_recent_work(tmp_path):
    a = Archive(tmp_path / "archive.sqlite")
    a.register("home", str(tmp_path))
    add(a, "home", "old", "user", "Work on memory-tool")
    packet = build_packet(a, "home", session="new")
    assert "Repository: none at startup directory" in packet.text
    assert "REPOSITORY HISTORY" not in packet.text
    assert "Work on memory-tool" in packet.text
    assert packet.selection["startup"]["recent_session_references"] == ["old"]
    a.close()


def test_existing_conversation_is_not_replaced_with_general_history(tmp_path):
    a = Archive(tmp_path / "archive.sqlite")
    a.register("main", str(tmp_path))
    add(a, "main", "old", "user", "UNRELATED_TOPIC")
    add(a, "main", "current", "user", "Continue watchdog fix")
    packet = build_packet(a, "main", session="current")
    assert "Continue watchdog fix" in packet.text
    assert "UNRELATED_TOPIC" not in packet.text
    assert "startup" not in packet.selection
    a.close()


def test_feedback_expiry_scaffolding_and_ambiguous_chats_are_excluded(tmp_path):
    a = Archive(tmp_path / "archive.sqlite")
    a.register("home", str(tmp_path))
    for verdict in ("noise", "wrong", "stale"):
        event = add(a, "home", verdict, "user", "EXCLUDED_" + verdict)
        a.add_feedback("home", verdict, "irrelevant", event)
    add(
        a, "home", "expired", "user", "EXCLUDED currently running PID 123", "2020-01-01"
    )
    add(a, "home", "harness", "user", "# AGENTS.md instructions\nEXCLUDED_harness")
    for chat in ("explicit1", "explicit2"):
        a.register(chat, str(tmp_path / "ambiguous"))
        add(a, chat, chat, "user", "EXCLUDED_ambiguous")
    add(a, "home", "kept", "user", "Retained source reference")
    p = build_packet(a, "home", session="new")
    assert "EXCLUDED" not in p.text
    assert "Retained source reference" in p.text
    a.close()


def test_startup_budgets_order_and_repeatability(tmp_path):
    a = Archive(tmp_path / "archive.sqlite")
    a.register("home", str(tmp_path))
    for i in range(15):
        # Insert oldest timestamp last; recency must not mean import order.
        add(
            a,
            "home",
            f"s{i}",
            "user",
            f"Topic {i} " + "long source " * 100,
            f"2026-10-07T12:{14 - i:02d}:00Z",
        )
    for budget in (512, 1500, 4000, 24000):
        p = build_packet(a, "home", budget, 0, session="new")
        assert p.tokens <= budget
        assert p.sha == build_packet(a, "home", budget, 0, session="new").sha
        refs = p.selection["startup"]["recent_session_references"]
        assert len(refs) <= 5
        if refs:
            assert refs[0] == "s0"
        for line in p.text.splitlines():
            if line.startswith('{"project"'):
                json.loads(line)
    a.close()


def test_worktree_git_files_and_nested_repos(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "fixture",
            "--allow-empty",
        ],
        check=True,
    )
    worktree = tmp_path / "worktree"
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "worktree",
            "add",
            "-q",
            "--detach",
            str(worktree),
        ],
        check=True,
    )
    (worktree / "src").mkdir()
    assert repository_root(worktree / "src") == str(worktree)
    nested = tmp_path / "nested"
    subprocess.run(["git", "init", "-q", str(nested)], check=True)
    assert repository_root(nested) == str(nested)


def test_empty_git_guard_is_not_a_repository(tmp_path):
    (tmp_path / ".git").mkdir()
    assert repository_root(tmp_path) is None
