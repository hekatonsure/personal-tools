"""Deterministic synthetic recovery corpus; no private conversation data."""

import json
import random
from datetime import datetime, timedelta, timezone


def populate(archive, project):
    archive.register("evaluation", str(project))
    archive.register("foreign", str(project / "foreign"))
    rng = random.Random(9371)
    base = datetime(2026, 10, 4, 19, tzinfo=timezone.utc)
    sequence = 0
    sources = {}

    def event(session, role, text, case=None, ts=None):
        nonlocal sequence
        sequence += 1
        date = ts or (base + timedelta(seconds=sequence)).isoformat()
        id = archive.append(
            "evaluation", role, text, f"native:{session}:{sequence}", ts=date
        )
        if case:
            sources[case] = id
        return id

    event("eval-memory", "user", "My original cloud spending cap is $5.", "original")
    event(
        "eval-memory",
        "user",
        "Correction: raise my cloud spending cap to $8 for that specific past experiment.",
        "current_cap",
    )
    event(
        "eval-memory",
        "user",
        "Use automatic compaction at 160000 tokens; retain working context between turns.",
        "threshold",
    )
    event(
        "eval-memory",
        "assistant",
        "The memory restart is VERIFIED. No further restart is required. The actual desktop received the restored packet.",
        "restart",
    )
    event(
        "eval-memory",
        "tool_result",
        "Saved and verified completed recovery report: recovery-final.json",
        "artifact",
    )
    event(
        "eval-memory",
        "user",
        "Always use uv for dependency management and Python scripts.",
        "stable_preference",
    )
    for i in range(120):
        event(
            "eval-memory",
            "tool_result",
            f"Build {i}: "
            + " ".join(f"item{rng.randrange(10000)}" for _ in range(120)),
        )
        if i % 8 == 0:
            event(
                "eval-memory",
                "assistant",
                f"Memory diagnostic batch {i} passed; no decision changed.",
            )
    document = "Recovery guide: retain context between turns to reuse cache. " * 100
    for i in range(45):
        event(
            "eval-memory",
            "tool_result",
            json.dumps({"output": document, "wall_time_seconds": i + 1}),
        )
        event(
            "eval-memory",
            "assistant",
            json.dumps(
                {
                    "risk_level": "low",
                    "user_authorization": "high",
                    "outcome": "allow",
                    "rationale": "Recovery document read.",
                }
            ),
        )
    event(
        "eval-memory",
        "tool_result",
        "Long diagnostic output\n"
        + ("unrelated diagnostic bytes\n" * 6000)
        + "TAIL_PROOF=plum-9371",
        "tail",
    )
    event(
        "eval-memory",
        "user",
        "Continue duplicate suppression for retrieval; that is the unfinished memory task.",
        "pending",
    )
    event(
        "eval-memory",
        "user",
        "Compare restoration, retrieval and restart behavior.",
        "focus",
    )
    # A different active chat in the SAME project must not become this one's tail.
    for i in range(100):
        event(
            "eval-soup",
            "tool_result",
            f"Competition run {i} "
            + " ".join(f"soup{rng.randrange(10000)}" for _ in range(180)),
        )
    event("eval-soup", "user", "Keep working on the replication experiment.")
    archive.append(
        "foreign", "user", "The unrelated project secret label is amber-foreign."
    )
    records = [
        {
            "id": "restart-before",
            "text": "Memory restart required.",
            "ts": "2026-10-04T20:00:00Z",
            "memory_kind": "status",
        },
        {
            "id": "restart-after",
            "text": "Memory restart verified; no further restart.",
            "ts": "2026-10-04T21:00:00Z",
        },
        {
            "id": "process",
            "text": "Memory process currently running PID 9371.",
            "ts": "2026-10-03T20:00:00Z",
            "memory_kind": "status",
        },
        {
            "id": "port-before",
            "text": "Memory listener port is 8000.",
            "ts": "2026-10-04T20:00:00Z",
            "claims": [{"key": "listener", "value": "8000"}],
        },
        {
            "id": "port-after",
            "text": "Memory listener port is 8001. No verification recorded.",
            "ts": "2026-10-04T21:00:00Z",
            "claims": [{"key": "listener", "value": "8001"}],
        },
        {
            "id": "preference",
            "text": "Always use uv.",
            "ts": "2020-01-01T00:00:00Z",
            "memory_kind": "preference",
        },
        {
            "id": "approval",
            "text": "The user approved a cloud rental for the experiment that ended yesterday. Historical permission does not authorize a new rental.",
            "ts": "2026-10-03T20:00:00Z",
        },
    ]
    for i in range(95):
        records.append(
            {
                "id": f"other-{i}",
                "session_id": "eval-soup",
                "text": f"Soup experiment {i} complete. "
                + " ".join(f"finding{rng.randrange(10000)}" for _ in range(140)),
                "ts": f"2026-10-04T21:{i % 60:02d}:00Z",
            }
        )
    with archive.db:
        for r in records:
            r = {"session_id": "eval-memory", "type": "pin", **r}
            archive.db.execute(
                "INSERT INTO notes VALUES(?,?,?)",
                ("evaluation", r["id"], json.dumps(r)),
            )
    return sources
