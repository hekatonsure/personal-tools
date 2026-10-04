"""Local snapshot audit; writes private text only inside the requested output folder."""

import argparse
import inspect
import json
from pathlib import Path
import sys
import time


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--db", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--jev", action="store_true")
    args = p.parse_args()
    sys.path.insert(0, str(args.source.resolve()))
    from memory_tool.archive import Archive
    from memory_tool.packing import build_packet
    from memory_tool.retrieval import search

    a = Archive(args.db)
    args.out.mkdir(parents=True, exist_ok=False)
    revised = "session" in inspect.signature(build_packet).parameters
    packets = []
    for budget in (12000, 24000, 64000):
        options = {"budget": budget, "recent_budget": 8000}
        if revised:
            options["session"] = "01a0c44d-6b7e-7be0-890b-fd236117e468"
        start = time.perf_counter()
        packet = build_packet(a, "bff-master", **options)
        packets.append(
            {
                "budget": budget,
                "tokens": packet.tokens,
                "milliseconds": round((time.perf_counter() - start) * 1000, 2),
                "selection": getattr(packet, "selection", None),
            }
        )
        (args.out / f"packet-{budget}.txt").write_text(packet.text, encoding="utf-8")
    query = "Why did we choose to retain working context across turns instead of clearing every turn? Cache hit performance tradeoff and staying under 200k."
    start = time.perf_counter()
    result = search(a, "bff-master", query, use_jev=args.jev)
    (args.out / "retrieval.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    meta = {
        "packets": packets,
        "retrieval_ms": round((time.perf_counter() - start) * 1000, 2),
        "retrieval": {k: v for k, v in result.items() if k != "hits"},
        "top5": [
            {k: v for k, v in h.items() if k != "text"} for h in result["hits"][:5]
        ],
    }
    (args.out / "metadata.json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8"
    )
    print(json.dumps(meta), flush=True)
    a.close()


if __name__ == "__main__":
    main()
