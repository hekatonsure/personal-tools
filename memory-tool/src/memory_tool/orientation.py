"""Repo-first startup evidence and a bounded directory of other conversations."""

import json
import os
import re
import subprocess
from collections import defaultdict
from pathlib import Path

from .archive import canonical, digest
from .selection import (
    LOW_VALUE,
    freshness,
    importance,
    normalized,
    session_of,
    timestamp,
    verdict,
)


def substantive_reply(rows):
    """Prefer a recent informative reply over an acknowledgement or opening plan.

    This is a presentation heuristic, not an assertion that work is complete.
    Retain a short reply as fallback so concise outcomes still have a source.
    """
    replies = [r for r in rows if r["role"] == "assistant"]
    informative = [
        r
        for r in replies
        if len(r["preview"].strip()) >= 80
        and not re.match(
            r"(?:I[’']ll\b|I will\b|I[’']m (?:using|going to)\b|Let me\b)",
            r["preview"].strip(),
            re.I,
        )
    ]
    finals = [r for r in informative if r.get("phase") in {"final", "final_answer"}]
    return (finals or informative or replies or [None])[-1]


def repository_root(project):
    path = Path(project).resolve()
    # Sandboxes can place empty .git guard directories above real workspaces.
    # Ask Git rather than mistaking those for repositories. Keep worktrees distinct.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=2,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return canonical(result.stdout.strip()) if result.returncode == 0 else None


def startup_orientation(archive, chat, session, counter, budget, now=None):
    """Only called for an empty conversation; never rebind or merge archives.

    Every reference carries its original project for MCP retrieval. Only uniquely
    routed chats are listed, so a reference cannot silently resolve to another chat.
    """
    project = archive.project(chat)
    repo = repository_root(project)
    groups = defaultdict(list)
    chats = archive.db.execute("SELECT id,project FROM chats ORDER BY id").fetchall()
    for row in chats:
        groups[row["project"]].append(row["id"])
    local, general = [], []
    for scope, ids in groups.items():
        if len(ids) != 1:
            # Explicit logical chats must not be conflated by project routing.
            continue
        same_repo = repo is not None and repository_root(scope) == repo
        conversations = defaultdict(list)
        for row in archive.event_index(ids[0]):
            sid = session_of(row)
            if not sid or sid == session or row["role"] not in {"user", "assistant"}:
                continue
            if verdict(row) in {"noise", "stale", "wrong"}:
                continue
            if freshness(row, now) in {"expired", "unverified_status"}:
                continue
            value = importance(row)
            if value is not None and value < LOW_VALUE:
                continue
            conversations[sid].append(row)
        for sid, rows in conversations.items():
            rows.sort(key=lambda r: (timestamp(r["ts"]) or 0, r["id"]))
            users = [r for r in rows if r["role"] == "user"]
            if not users:
                continue
            topic = users[0]
            latest = rows[-1]
            reply = substantive_reply(rows)
            reference = {
                "project": scope,
                "session": sid,
                "last_seen": latest["ts"],
                "topic_event": topic["id"],
                "topic_excerpt": topic["preview"][:200],
                "latest_event": latest["id"],
                # Full topic identity prevents shared truncated prefixes from
                # merging unrelated sessions. It is not included in the packet.
                "_topic": digest(
                    normalized(archive.event(ids[0], topic["id"])["text"])
                ),
                "_disposable": bool(
                    re.search(
                        r"\bdisposable (?:installation )?smoke test\b",
                        topic["preview"],
                        re.I,
                    )
                ),
                "_turns": len({normalized(r["preview"]) for r in users}),
            }
            if reply:
                reference["last_reply"] = {
                    "event": reply["id"],
                    "ts": reply["ts"],
                    "freshness": freshness(reply, now),
                    "excerpt": reply["preview"],
                }
            if same_repo:
                chosen = {r["id"]: r for r in [topic, users[-1]]}
                reference["evidence"] = [
                    {
                        "event": r["id"],
                        "role": r["role"],
                        "ts": r["ts"],
                        "freshness": freshness(r, now),
                        "excerpt": r["preview"],
                    }
                    for r in sorted(chosen.values(), key=lambda r: r["id"])
                ]
            (local if same_repo else general).append(reference)

    def newest(rows):
        return sorted(
            rows,
            key=lambda r: (timestamp(r["last_seen"]) or 0, r["latest_event"]),
            reverse=True,
        )

    def section(title, rows, limit, allowance):
        text, selected = "", []
        groups = {}
        # Explicit disposable connection probes are fallback orientation only.
        # Ordinary conversations about testing remain ordinary history.
        for row in sorted(newest(rows), key=lambda r: r["_disposable"]):
            key = (row["project"], row["_topic"])
            groups.setdefault(key, []).append(row)
        for members in groups.values():
            latest = members[0]
            row = {k: v for k, v in latest.items() if not k.startswith("_")}
            if len(members) > 1:
                row["related_sessions"] = len(members) - 1
                # Fork/snapshot sessions often contain only the opening request.
                # Carry a reply from the fuller conversation too, with its own
                # date and session. Never claim it supersedes the newest reply.
                fuller = max(members, key=lambda r: r["_turns"])
                if (
                    fuller["_turns"] > latest["_turns"]
                    and fuller.get("last_reply")
                    and fuller["last_reply"]["excerpt"]
                    != latest.get("last_reply", {}).get("excerpt")
                ):
                    row["related_reply"] = {
                        "session": fuller["session"],
                        **fuller["last_reply"],
                    }
            line = json.dumps(row, ensure_ascii=False) + "\n"
            candidate = (text or title) + line
            if counter.count(candidate) <= allowance:
                text = candidate
                selected.append(row["session"])
            if len(selected) == limit:
                break
        return text, selected

    header = (
        "\nSTARTUP ORIENTATION\n"
        + (
            f"Repository: {repo}\n"
            if repo
            else "Repository: none at startup directory.\n"
        )
        + "Dated excerpts and references, not current tasks or authorization. "
        "Use memory_zoom with each reference's project and event, or memory_search "
        "with that project. Original archive scopes are unchanged.\n"
    )
    available = max(0, budget - counter.count(header) - 8)
    repo_text, repo_sessions = section(
        "\nREPOSITORY HISTORY\n", local, 3, available * 3 // 4 if general else available
    )
    recent_text, recent_sessions = section(
        "\nRECENT SESSION REFERENCES (other locations)\n",
        general,
        5,
        min(2400, available - counter.count(repo_text)),
    )
    text = header + repo_text + recent_text if repo_text or recent_text else ""
    return text, {
        "repository": repo,
        "repository_sessions": repo_sessions,
        "recent_session_references": recent_sessions,
    }
