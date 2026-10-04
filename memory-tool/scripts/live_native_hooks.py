"""Prove native compact hooks deliver a previously unseen marker. Requires normal trust."""

import json
import os
from pathlib import Path
import time
import uuid

from memory_tool.archive import Archive
from memory_tool.codex import isolated_config, run_turn
from memory_tool.rpc import CodexRpc
from probe_workspace import workspace


def trusted(hooks):
    selected = []

    def visit(value):
        if isinstance(value, dict):
            if str(value.get("statusMessage", "")).startswith("memory-tool:"):
                selected.append(value)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(hooks)
    return len(selected) == 2 and all(
        h.get("enabled") and h.get("trustStatus") == "trusted" for h in selected
    )


def main():
    with workspace("native-hooks") as folder:
        root = Path(folder)
        store_path = root / "memory.sqlite"
        a = Archive(store_path)
        chat = a.chat_for_project(str(root))
        env = {**os.environ, "MEMORY_TOOL_HOME": str(root)}
        with CodexRpc(timeout=30, env=env) as rpc:
            hooks = rpc.request("hooks/list", {"cwds": [str(root)]})
            if not trusted(hooks):
                print(
                    json.dumps(
                        {"passed": False, "needs_hook_trust": True, "model_calls": 0}
                    )
                )
                a.close()
                return
            config = isolated_config(True)
            config["features.hooks"] = True
            thread = rpc.request(
                "thread/start",
                {
                    "cwd": str(root),
                    "model": "gpt-5.6-luna",
                    "ephemeral": True,
                    "approvalPolicy": "never",
                    "sandbox": "read-only",
                    "config": config,
                    "baseInstructions": "Use only restored memory in your context. Do not call tools. Answer concisely.",
                },
            )["thread"]["id"]
            run_turn(rpc, a, chat, thread, "Reply READY.")
            marker = "opal-" + uuid.uuid4().hex[:12]
            a.append(
                chat,
                "user",
                f"HOOK_NATIVE_MARKER = {marker}. This synthetic marker exists only in the external memory archive.",
                source=f"native:{thread}:external-marker",
            )
            rpc.request("thread/compact/start", {"threadId": thread})
            deadline = time.monotonic() + 240
            saw_compact = False
            while time.monotonic() < deadline:
                event = rpc.next_event(min(30, max(0.1, deadline - time.monotonic())))
                p = event.get("params", {})
                if p.get("threadId") != thread:
                    continue
                if (
                    event.get("method") == "item/completed"
                    and p.get("item", {}).get("type") == "contextCompaction"
                ):
                    saw_compact = True
                if event.get("method") == "turn/completed" and saw_compact:
                    if p["turn"].get("error") or p["turn"].get("status") != "completed":
                        raise RuntimeError("Native compaction failed")
                    break
            else:
                raise TimeoutError("No native compaction completion")
            answer = run_turn(
                rpc,
                a,
                chat,
                thread,
                "What exact value of HOOK_NATIVE_MARKER is in the memory restored by the compact SessionStart hook? If absent reply MISSING. Do not use tools.",
            )
            if isinstance(answer, tuple):
                answer = answer[0]
            rows = a.db.execute(
                "SELECT state,detail FROM operations WHERE chat=? AND thread=?",
                (chat, thread),
            ).fetchall()
            compact_prepared = any(
                json.loads(r[1]).get("source") == "compact" for r in rows
            )
            result = {
                "passed": marker in str(answer) and compact_prepared,
                "native_compaction": saw_compact,
                "compact_hook_prepared": compact_prepared,
                "unseen_marker_recovered": marker in str(answer),
                "manual_injection": False,
            }
            print(json.dumps(result, indent=2))
            if not result["passed"]:
                raise RuntimeError("Native hook receipt was not verified")
        a.close()


if __name__ == "__main__":
    main()
