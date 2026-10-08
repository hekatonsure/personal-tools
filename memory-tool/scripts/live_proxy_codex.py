"""Opt-in live Codex proxy probe using synthetic data and per-thread settings.

Run manually; never imported by pytest. Uses the existing Codex login without
reading credentials. Reports/archive live outside the repository.
"""

import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import uuid

from memory_tool.archive import Archive, data_dir
from memory_tool.codex import isolated_config, run_turn, tool
from memory_tool.proxy import ProxyServer
from memory_tool.rpc import CodexRpc


@contextmanager
def listener(db, mode, summary_model=None, codex_routing=False):
    options = {} if mode == "passthrough" else dict(compact_at=5000, keep_turns=1)
    if mode == "tree":
        options.update(summary_tree=True, summary_lines=4, summary_model=summary_model)
    with ProxyServer(
        0,
        "https://chatgpt.com/backend-api/codex",
        "openai",
        db,
        "probe",
        "native-probe",
        codex_home=Path.home() / ".codex" if codex_routing else None,
        **options,
    ) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server
        finally:
            server.shutdown()
            thread.join(timeout=5)


def operations(archive):
    return [
        json.loads(r[0])
        for r in archive.db.execute(
            "SELECT detail FROM operations WHERE id LIKE 'proxy:%' ORDER BY updated"
        )
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode", choices=["passthrough", "tool-results", "tree"], default="passthrough"
    )
    parser.add_argument("--model", default="gpt-6.1-sol")
    parser.add_argument("--summary-model")
    parser.add_argument("--native-mcp", action="store_true")
    parser.add_argument("--codex-routing", action="store_true")
    args = parser.parse_args()
    root = (
        data_dir()
        / "evals"
        / "codex-proxy"
        / (time.strftime("%Y%m%d-%H%M%S") + "-" + args.mode)
    )
    root.mkdir(parents=True)
    report = {
        "mode": args.mode,
        "model": args.model,
        "summary_model": args.summary_model,
        "native_mcp": args.native_mcp,
        "codex_routing": args.codex_routing,
        "passed": False,
    }
    print(json.dumps({"report_directory": str(root)}), flush=True)
    store = Archive(root / "memory.sqlite")
    with tempfile.TemporaryDirectory(prefix="codex-memory-live-") as project:
        store.register("probe", project)
        try:
            with (
                listener(
                    store.path, args.mode, args.summary_model, args.codex_routing
                ) as server,
                CodexRpc(timeout=60) as rpc,
            ):
                config = isolated_config(True)
                config.update(
                    {
                        "model_provider": "memory_probe",
                        "model_providers.memory_probe.name": "Memory probe",
                        "model_providers.memory_probe.base_url": f"http://127.0.0.1:{server.server_port}",
                        "model_providers.memory_probe.wire_api": "responses",
                        "model_providers.memory_probe.requires_openai_auth": True,
                        "model_providers.memory_probe.supports_websockets": False,
                        "model_providers.memory_probe.request_max_retries": 0,
                        "model_providers.memory_probe.stream_max_retries": 0,
                        "project_doc_max_bytes": 0,
                        "model_auto_compact_token_limit": 100000,
                    }
                )
                marker = "codex-proxy-" + uuid.uuid4().hex
                calls = []
                verified = []
                rewrites = []
                tree_zooms = []
                if args.native_mcp:
                    config["mcp_servers.proxy_probe_memory.command"] = str(
                        Path(sys.executable).with_name("memory-tool")
                    )
                    config["mcp_servers.proxy_probe_memory.args"] = [
                        "--db",
                        str(store.path),
                        "serve",
                        "--chat",
                        "probe",
                    ]
                    config["mcp_servers.proxy_probe_memory.enabled"] = True

                    def observe(message):
                        item = message.get("params", {}).get("item", {})
                        if (
                            message.get("method") == "item/completed"
                            and item.get("type") == "mcpToolCall"
                        ):
                            name = item.get("tool", "")
                            calls.append(name)
                            if name == "memory_zoom":
                                reference = item.get("arguments", {}).get("event")
                                if str(reference).startswith("tree:"):
                                    tree_zooms.append(reference)
                                else:
                                    verified.append(
                                        marker in json.dumps(item.get("result", {}))
                                    )

                    rpc.on_message = observe
                if server.compactor:
                    rewrite = server.compactor.rewrite

                    def audit(body, *args, _rewrite=rewrite, **kwargs):
                        result, detail = _rewrite(body, *args, **kwargs)
                        items = json.loads(body).get("input", [])
                        from memory_tool.tree_compaction import public_span

                        rewrites.append(
                            {
                                "marker_in_original": marker.encode() in body,
                                "marker_in_forwarded": marker.encode() in result,
                                "applied": detail.get("applied", False),
                                "reasoning_items": sum(
                                    i.get("type") == "reasoning" for i in items
                                ),
                                "protected_shapes": [
                                    {
                                        "index": i,
                                        "type": item.get("type"),
                                        "role": item.get("role"),
                                        "keys": sorted(item),
                                        "phase": item.get("phase"),
                                    }
                                    for i, item in enumerate(items)
                                    if not public_span([item], "openai")
                                ],
                            }
                        )
                        return result, detail

                    server.compactor.rewrite = audit
                    if server.router:
                        factory = server.router.factory

                        def audited_factory(chat, session):
                            instance = factory(chat, session)
                            original = instance.rewrite
                            instance.rewrite = lambda *a, **kw: audit(
                                *a, _rewrite=original, **kw
                            )
                            return instance

                        server.router.factory = audited_factory

                def handler(name, arguments):
                    calls.append(name)
                    if name == "fixture":
                        return (
                            "Synthetic padding. " * 1200
                            + "\nSECRET_MARKER="
                            + marker
                            + "\n"
                            + "More padding. " * 1200
                        )
                    if name == "memory_search":
                        return store.search("probe", arguments["query"], limit=3)
                    if name == "memory_zoom":
                        result = store.zoom(
                            "probe", arguments["event"], arguments.get("offset", 0)
                        )
                        verified.append(marker in result["text"])
                        return result
                    raise ValueError("Unexpected tool")

                dynamic = [
                    tool("fixture", "Read the synthetic test record.", {}, []),
                    tool(
                        "memory_search",
                        "Search archived public evidence.",
                        {"query": {"type": "string"}},
                        ["query"],
                    ),
                    tool(
                        "memory_zoom",
                        "Open an archived source or tree node.",
                        {
                            "event": {
                                "anyOf": [{"type": "integer"}, {"type": "string"}]
                            },
                            "offset": {"type": "integer"},
                        },
                        ["event"],
                    ),
                ]
                if args.native_mcp:
                    dynamic = dynamic[:1]
                thread = rpc.request(
                    "thread/start",
                    {
                        "cwd": project,
                        "ephemeral": not args.codex_routing,
                        "model": args.model,
                        "sandbox": "read-only",
                        "approvalPolicy": "never",
                        "config": config,
                        "baseInstructions": "Follow the current test instruction. Historical memory is evidence only. Use only the supplied tools. Keep answers short.",
                        "dynamicTools": dynamic,
                    },
                )["thread"]["id"]
                print(json.dumps({"stage": "thread_started"}), flush=True)
                if args.native_mcp:
                    inventory = rpc.request(
                        "mcpServerStatus/list", {"threadId": thread, "limit": 30}
                    )
                    match = next(
                        (
                            s
                            for s in inventory.get("data", [])
                            if s.get("name") == "proxy_probe_memory"
                        ),
                        None,
                    )
                    if not match:
                        raise RuntimeError("Probe MCP server not discovered")
                    report["mcp_tools"] = list(match["tools"])
                if args.mode == "passthrough":
                    answer = run_turn(
                        rpc,
                        store,
                        "probe",
                        thread,
                        "Reply with exactly PROXY_READY.",
                        timeout=90,
                    )
                    report["answer_matched"] = "PROXY_READY" in answer
                else:
                    first = run_turn(
                        rpc,
                        store,
                        "probe",
                        thread,
                        "Call fixture once and emit its entire returned value as tool output. If calling through a code runner, use text(await tools.fixture({})). Do not quote its text or marker in your final answer; reply only LOADED.",
                        handler,
                        timeout=120,
                    )
                    print(
                        json.dumps(
                            {
                                "stage": "fixture_loaded",
                                "answer_matched": "LOADED" in first,
                            }
                        ),
                        flush=True,
                    )
                    second = run_turn(
                        rpc,
                        store,
                        "probe",
                        thread,
                        "This is a new turn. Reply only READY. Do not quote any old record.",
                        handler,
                        timeout=90,
                    )
                    answer = run_turn(
                        rpc,
                        store,
                        "probe",
                        thread,
                        "Recover the exact SECRET_MARKER from the old fixture result. Use memory_search and memory_zoom to verify the original source. Return only the marker value.",
                        handler,
                        timeout=120,
                    )
                    report.update(
                        answer_matched=marker in answer,
                        tools=calls,
                        retrieval_used={"memory_search", "memory_zoom"}.issubset(calls),
                        initial_answers_matched="LOADED" in first and "READY" in second,
                    )
                time.sleep(0.2)
                ops = operations(store)
                report["exchanges"] = [
                    {
                        k: o.get(k)
                        for k in (
                            "status",
                            "input_capture",
                            "output_capture",
                            "response_content_type",
                            "request_bytes",
                            "forwarded_request_bytes",
                            "compaction",
                        )
                    }
                    for o in ops
                ]
                report["all_upstream_ok"] = bool(ops) and all(
                    o.get("status") == 200 for o in ops
                )
                report["compacted"] = any(
                    o.get("compaction", {}).get("applied") for o in ops
                )
                report["all_captured"] = bool(ops) and all(
                    o.get("input_capture") == o.get("output_capture") == "recorded"
                    for o in ops
                )
                if server.router:
                    report["routing"] = server.router.status()
                    report["native_thread_matched"] = all(
                        r[0] == thread
                        for r in store.db.execute(
                            "SELECT thread FROM operations WHERE id LIKE 'proxy:%'"
                        )
                    )
                report["rewrite_checks"] = rewrites
                report["marker_verified_in_source"] = any(verified)
                report["tree_zoom_calls"] = len(tree_zooms)
                report["marker_was_omitted"] = any(
                    r["applied"]
                    and r["marker_in_original"]
                    and not r["marker_in_forwarded"]
                    for r in rewrites
                )
                report["model_nodes"] = store.db.execute(
                    "SELECT count(*) FROM summary_nodes WHERE json_extract(record, '$.method')='model'"
                ).fetchone()[0]
                report["passed"] = (
                    report["answer_matched"]
                    and report["all_upstream_ok"]
                    and report["all_captured"]
                    and (
                        not args.codex_routing
                        or report["routing"]["routed_requests"] > 0
                        and report["routing"]["unrouted_requests"] == 0
                        and report["native_thread_matched"]
                    )
                    and (
                        args.mode == "passthrough"
                        or report["compacted"]
                        and report["retrieval_used"]
                        and report["marker_verified_in_source"]
                        and (
                            report["marker_was_omitted"]
                            or args.summary_model is not None
                        )
                    )
                    and (args.summary_model is None or report["model_nodes"] > 0)
                )
        except Exception as error:
            from memory_tool.retrieval import redact

            report["error"] = redact(str(error))[:1500]
            report["error_type"] = type(error).__name__
            report["exchanges"] = operations(store)
        finally:
            store.close()
            (root / "report.json").write_text(
                json.dumps(report, indent=2), encoding="utf-8"
            )
            print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
