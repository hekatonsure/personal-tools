"""Run one version/budget against the predeclared protocol; optional live reader."""

import argparse
import hashlib
import inspect
import json
from pathlib import Path
import sys
import time

from selection_corpus import populate


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--budget", type=int, choices=[12000, 24000, 64000], required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--live", action="store_true")
    args = p.parse_args()
    sys.path.insert(0, str(args.source.resolve()))
    from memory_tool.archive import Archive
    from memory_tool.packing import build_packet
    from memory_tool.retrieval import search

    protocol_path = Path(__file__).with_name("selection_protocol.json")
    protocol = json.loads(protocol_path.read_text())
    args.out.mkdir(parents=True, exist_ok=False)
    a = Archive(args.out / "source.sqlite")
    sources = populate(a, args.out.resolve())
    options = {"budget": args.budget, "recent_budget": protocol["recent_budget"]}
    current = "session" in inspect.signature(build_packet).parameters
    if current:
        options.update(session="eval-memory", now=1791151200)
    t = time.perf_counter()
    packet = build_packet(a, "evaluation", **options)
    elapsed = time.perf_counter() - t
    (args.out / "packet.txt").write_text(packet.text, encoding="utf-8")
    recall = {}
    for case in protocol["cases"]:
        if case["id"] in sources:
            rows = a.search("evaluation", case["question"])
            recall[case["id"]] = any(r["event"] == sources[case["id"]] for r in rows)
    result = {
        "implementation_sha256": hashlib.sha256(
            b"".join(
                str(path.relative_to(args.source)).encode() + b"\0" + path.read_bytes()
                for path in sorted(args.source.rglob("*.py"))
            )
        ).hexdigest(),
        "version": "0.3.0" if current else "0.2.0",
        "budget": args.budget,
        "protocol_sha256": hashlib.sha256(protocol_path.read_bytes()).hexdigest(),
        "corpus_sha256": hashlib.sha256(
            Path(__file__).with_name("selection_corpus.py").read_bytes()
        ).hexdigest(),
        "packet_tokens": packet.tokens,
        "packet_build_ms": round(elapsed * 1000, 2),
        "source_candidate_recall": recall,
        "retrieval_calls": [],
    }
    if args.live:
        from memory_tool.codex import TOOLS, isolated_config, run_turn
        from memory_tool.rpc import CodexRpc

        a.register("reader", str(args.out.resolve()))

        def handler(name, arguments):
            result["retrieval_calls"].append({"name": name, "arguments": arguments})
            if len(result["retrieval_calls"]) > 12:
                return {"error": "retrieval budget exhausted"}
            if name == "memory_search":
                return search(a, "evaluation", arguments["query"])
            if name == "memory_zoom":
                return a.zoom(
                    "evaluation", arguments["event"], arguments.get("offset", 0)
                )
            raise ValueError("Unknown evaluation tool")

        instructions = (
            "Answer questions using the provided historical memory and read-only archive tools. "
            "Never infer current permission or current process status from an old record. "
            "Use search/zoom if required. Do not guess missing evidence. "
            "Return only a JSON object mapping each question id to its short answer: "
            "numbers as strings without units, filenames exactly; use UNKNOWN when evidence is absent, "
            "UNRESOLVED for conflicting unverified claims, VERIFIED for a verified restart, NO for no authorization."
        )
        questions = [
            {"id": c["id"], "question": c["question"]} for c in protocol["cases"]
        ]
        usage = []
        start = time.perf_counter()
        with CodexRpc(timeout=45) as rpc:
            thread = rpc.request(
                "thread/start",
                {
                    "cwd": str(args.out.resolve()),
                    "model": "gpt-5.6-luna",
                    "ephemeral": True,
                    "approvalPolicy": "never",
                    "sandbox": "read-only",
                    "config": isolated_config(True),
                    "baseInstructions": instructions,
                    "developerInstructions": packet.text,
                    "dynamicTools": TOOLS[:2],
                },
            )["thread"]["id"]
            answer = run_turn(
                rpc,
                a,
                "reader",
                thread,
                json.dumps(questions),
                handler,
                timeout=240,
                master=True,
                usage=usage,
            )
        if isinstance(answer, tuple):
            answer = answer[0]
        clean = (
            answer.strip()
            .removeprefix("```json")
            .removeprefix("```")
            .removesuffix("```")
            .strip()
        )
        try:
            parsed = json.loads(clean)
        except ValueError:
            parsed = {}
        result.update(
            {
                "answer": answer,
                "case_correct": {
                    c["id"]: str(parsed.get(c["id"], "")).casefold()
                    == c["expected"].casefold()
                    for c in protocol["cases"]
                },
                "model": "gpt-5.6-luna",
                "model_wall_seconds": round(time.perf_counter() - start, 3),
                "usage": usage[-1].get("total", {}) if usage else {},
            }
        )
        result["exact_answer_accuracy"] = sum(result["case_correct"].values()) / len(
            protocol["cases"]
        )
        result["retrieval_attempts"] = len(result["retrieval_calls"])
        result["retrieval_executed"] = min(12, len(result["retrieval_calls"]))
    (args.out / "result.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {k: v for k, v in result.items() if k not in {"answer", "retrieval_calls"}}
        ),
        flush=True,
    )
    a.close()


if __name__ == "__main__":
    main()
