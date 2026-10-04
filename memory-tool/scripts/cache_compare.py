"""Small matched live cache probe; not a billing or large-context benchmark."""

import json
from pathlib import Path
import random
import tempfile
import time

from memory_tool.archive import Archive
from memory_tool.codex import CodexGateway


def main():
    reports = []
    policies = [("adaptive", 160000), ("fresh", 160000), ("adaptive", 10000)]
    prompts = [
        "Remember that today's chosen project label is cedar-41. Reply with only cedar-41.",
        "What is today's chosen project label? Reply with only the label.",
        "Again, return only today's chosen project label.",
        "Confirm today's chosen project label one last time. Return only the label.",
    ]
    with tempfile.TemporaryDirectory(prefix="memory-tool-cache-") as folder:
        project = Path(folder)
        for index, (mode, threshold) in enumerate(policies):
            store = Archive(project / f"policy-{index}.sqlite")
            store.register("matched-chat", str(project))
            rng = random.Random(9371)
            for event in range(90):
                store.append(
                    "matched-chat",
                    "tool_result",
                    f"Synthetic record {event}: "
                    + " ".join(f"item{rng.randrange(1000)}" for _ in range(45)),
                )
            gateway = CodexGateway(
                store,
                "matched-chat",
                model="gpt-5.6-luna",
                budget=6400,
                recent=2000,
                mode=mode,
                reset_at=threshold,
            )
            traces = []
            try:
                for prompt in prompts:
                    start = time.perf_counter()
                    answer = gateway.turn(prompt)
                    assert "cedar-41" in answer, answer
                    trace = gateway.last_trace
                    traces.append(
                        {
                            "seconds": round(time.perf_counter() - start, 3),
                            "reused": trace["reused"],
                            "history_tokens": trace["tokens"],
                            "estimated_context_tokens": trace[
                                "estimated_context_tokens"
                            ],
                            "usage": trace["usage_delta"],
                        }
                    )
                reports.append(
                    {
                        "mode": mode,
                        "reset_at": threshold,
                        "turns": traces,
                        "input_tokens": sum(
                            t["usage"].get("inputTokens", 0) for t in traces
                        ),
                        "cached_input_tokens": sum(
                            t["usage"].get("cachedInputTokens", 0) for t in traces
                        ),
                        "seconds": round(sum(t["seconds"] for t in traces), 3),
                    }
                )
                print(json.dumps(reports[-1]), flush=True)
            finally:
                gateway.close()
                store.close()
    print(
        json.dumps(
            {
                "matched_seed": 9371,
                "matched_turns": 4,
                "history_budget": 6400,
                "recent_budget": 2000,
                "model": "gpt-5.6-luna",
                "policies": reports,
                "limits": "One sequential trial per policy, shared provider cache, short answers, no 200k stress run. Timing/order is confounded; cache tokens are reported observations.",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
