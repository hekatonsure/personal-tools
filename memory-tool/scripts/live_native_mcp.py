"""Read-only proof that a native Codex context discovers and uses installed memory tools."""

import argparse
import json
import os
from pathlib import Path
import tempfile
import tomllib

from memory_tool.archive import Archive
from memory_tool.codex import isolated_config, run_turn
from memory_tool.rpc import CodexRpc


def trace(message):
    method = message.get("method", "response")
    params = message.get("params", {})
    if method in {"item/started", "item/completed", "turn/completed", "error"} or (
        "id" in message and "method" in message
    ):
        print(
            json.dumps(
                {
                    "event": method,
                    "item": params.get("item", {}).get("type"),
                    "tool": params.get("item", {}).get("tool"),
                    "request_id": message.get("id"),
                    "parameter_names": list(params) if "id" in message else None,
                }
            ),
            flush=True,
        )


parser = argparse.ArgumentParser()
parser.add_argument("--query", default="configured provider total cap")
parser.add_argument("--expect", default="$5")
parser.add_argument(
    "--local",
    action="store_true",
    help="Disable optional Jev in this disposable probe only",
)
args = parser.parse_args()
task_codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
registered = tomllib.loads(
    (task_codex_home / "config.toml").read_text(encoding="utf-8")
)["mcp_servers"]["memory_tool"]
registered_args = registered["args"]
chat = registered_args[registered_args.index("--chat") + 1]
archive = Archive()
project = archive.project(chat)
archive.close()
with tempfile.TemporaryDirectory(prefix="memory-tool-mcp-probe-") as folder:
    store = Archive(Path(folder) / "probe.sqlite")
    store.register("probe", project)
    try:
        with CodexRpc(on_message=trace, timeout=60) as rpc:
            config = isolated_config(True)
            config["mcp_servers.memory_tool.enabled"] = True
            config["mcp_servers.memory_tool.tool_timeout_sec"] = 30
            config["mcp_servers.memory_tool.env.MEMORY_TOOL_DIAGNOSTICS"] = str(
                Path(folder) / "metadata.log"
            )
            if args.local:
                config["mcp_servers.memory_tool.args"] = [
                    value for value in registered_args if value != "--jev"
                ]
            thread = rpc.request(
                "thread/start",
                {
                    "cwd": project,
                    "model": "gpt-5.6-luna",
                    "ephemeral": True,
                    "approvalPolicy": "never",
                    "sandbox": "read-only",
                    "config": config,
                    "baseInstructions": "Use only the memory_tool MCP tools for historical evidence. Return concise findings. Do not run commands or change files.",
                },
            )["thread"]["id"]
            inventory = rpc.request(
                "mcpServerStatus/list", {"threadId": thread, "limit": 20}
            )
            available = next(
                (
                    r
                    for r in inventory.get("data", [])
                    if r.get("name") == "memory_tool"
                ),
                None,
            )
            if not available:
                raise RuntimeError("memory_tool missing from native MCP inventory")
            print(
                json.dumps(
                    {
                        "runtime_status": available["runtimeStatus"],
                        "tools": list(available["tools"]),
                    }
                ),
                flush=True,
            )
            direct = rpc.request(
                "mcpServer/tool/call",
                {
                    "threadId": thread,
                    "server": "memory_tool",
                    "tool": "memory_status",
                    "arguments": {},
                },
            )
            assert not direct.get("isError"), direct
            print(json.dumps({"direct_status": True}), flush=True)
            direct_search = rpc.request(
                "mcpServer/tool/call",
                {
                    "threadId": thread,
                    "server": "memory_tool",
                    "tool": "memory_search",
                    "arguments": {"query": args.query},
                },
            )
            assert not direct_search.get("isError"), direct_search
            print(json.dumps({"direct_search": True}), flush=True)
            answer = run_turn(
                rpc,
                store,
                "probe",
                thread,
                f"Use memory_status to verify the archive, then memory_search for {json.dumps(args.query)}. Report the answer with its source event ID. Treat it as historical evidence.",
                timeout=90,
            )
            calls = [
                json.loads(r["text"])
                for r in store.events("probe")
                if r["role"] == "tool_result"
            ]
            mcp = [r for r in calls if r.get("type") == "mcpToolCall"]
            assert any("memory_status" in r.get("tool", "") for r in mcp), mcp
            assert any("memory_search" in r.get("tool", "") for r in mcp), mcp
            assert args.expect in answer, answer
            print(
                json.dumps(
                    {
                        "passed": True,
                        "server": "memory_tool",
                        "tools": [r["tool"] for r in mcp],
                        "recovered_earlier_cap": True,
                        "answer": answer,
                    },
                    indent=2,
                )
            )
    finally:
        store.close()
