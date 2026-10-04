"""Opt-in integration probe using disposable contexts and synthetic evidence."""

import json
from contextlib import ExitStack
from probe_workspace import workspace

from memory_tool.archive import Archive
from memory_tool.codex import (
    CodexGateway,
    compact_and_restore,
    isolated_config,
    run_turn,
)
from memory_tool.packing import build_packet
from memory_tool.rpc import CodexRpc


def main():
    report = {}
    with (
        workspace() as project,
        ExitStack() as cleanup,
    ):
        store = Archive(project / "memory.sqlite")
        cleanup.callback(store.close)
        store.register("recovery", str(project))
        store.append(
            "recovery",
            "user",
            "Preface " * 250 + "RECOVERY_PROOF_8392 = copper-kite-9371",
        )
        for index in range(160):
            store.append(
                "recovery", "tool_result", f"Unrelated synthetic row {index}. " * 12
            )
        assert "copper-kite-9371" not in build_packet(store, "recovery", 1500, 400).text
        gateway = CodexGateway(
            store,
            "recovery",
            model="gpt-5.6-luna",
            budget=1500,
            recent=400,
            mode="fresh",
        )
        cleanup.callback(gateway.close)
        answer = gateway.turn(
            "Find the exact value of RECOVERY_PROOF_8392 in old chat history. Use memory_search and then memory_zoom to verify the source. Return the value."
        )
        assert "copper-kite-9371" in answer, answer
        trace1 = gateway.last_trace
        tool_names = [
            json.loads(r["text"])["tool"]
            for r in store.events("recovery")
            if r["role"] == "tool_call"
        ]
        assert {"memory_search", "memory_zoom"}.issubset(tool_names), tool_names
        store.close()
        store = Archive(project / "memory.sqlite")
        cleanup.callback(store.close)
        restarted = CodexGateway(
            store,
            "recovery",
            model="gpt-5.6-luna",
            budget=1500,
            recent=400,
            mode="fresh",
        )
        cleanup.callback(restarted.close)
        second = restarted.turn(
            "After restarting the gateway, what was the recovered value of RECOVERY_PROOF_8392?"
        )
        assert "copper-kite-9371" in second, second
        assert restarted.last_trace["thread"] != trace1["thread"]
        report["fresh_context_recovery"] = {
            "passed": True,
            "tools": tool_names,
            "different_backend_threads": True,
            "before_tokens": trace1["tokens"],
            "after_tokens": restarted.last_trace["tokens"],
            "source_not_in_initial_packet": True,
            "restart_recovery": True,
        }
        (project / "evidence.txt").write_text(
            "DELEGATION_PROOF_4892=orchid-18", encoding="utf-8"
        )
        delegated = restarted.turn(
            "Use delegate to read evidence.txt in the project and report the DELEGATION_PROOF_4892 value. The master must not read files directly."
        )
        assert "orchid-18" in delegated, delegated
        assert restarted.worker_calls >= 1
        report["delegation"] = {
            "passed": True,
            "worker_calls": restarted.worker_calls,
            "worker_write": False,
        }
        writer = CodexGateway(
            store,
            "recovery",
            model="gpt-5.6-luna",
            budget=1500,
            recent=400,
            mode="fresh",
            worker_write=True,
        )
        cleanup.callback(writer.close)
        writer.turn(
            "Use delegate to create worker-output.txt in the project containing exactly WORKER_WRITE_PROOF_5831=maple-22. The master must delegate this file work. Confirm completion."
        )
        assert (project / "worker-output.txt").read_text(
            encoding="utf-8"
        ).strip() == "WORKER_WRITE_PROOF_5831=maple-22"
        assert writer.worker_calls >= 1
        report["delegated_write"] = {
            "passed": True,
            "worker_calls": writer.worker_calls,
            "scope": "disposable project",
        }
        store.register("native", str(project))
        store.append(
            "native",
            "user",
            "The native compaction recovery marker is NATIVE_PROOF_2861=amber-73.",
        )
        with CodexRpc() as rpc:
            native = rpc.request(
                "thread/start",
                {
                    "cwd": str(project),
                    "ephemeral": True,
                    "model": "gpt-5.6-luna",
                    "sandbox": "read-only",
                    "approvalPolicy": "never",
                    "config": isolated_config(True),
                    "baseInstructions": "Reply to the user concisely.",
                },
            )["thread"]["id"]
            run_turn(
                rpc,
                store,
                "native",
                native,
                "The marker is NATIVE_PROOF_2861=amber-73. Acknowledge it.",
            )
            restored = compact_and_restore(
                rpc, store, "native", native, budget=1500, recent=400
            )
            final = run_turn(
                rpc,
                store,
                "native",
                native,
                "What was the exact NATIVE_PROOF_2861 value?",
            )
            assert "amber-73" in final, final
            report["native_compaction"] = {
                "passed": True,
                "completion_confirmed": True,
                "injection_confirmed": True,
                "recovered_marker": True,
                "empty_context": restored["empty_context"],
            }
        store.close()
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
