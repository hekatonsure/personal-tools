"""Bounded, read-only retrieval for native clients. Large packets are not tool results."""

import json
import os
from pathlib import Path
import sys
import time

from .packing import checkpoint


TOOLS = [
    {
        "name": "memory_status",
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": "Archive scope and size; metadata only.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "memory_search",
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
        "description": "Find exact evidence in this configured logical chat.",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "memory_zoom",
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": "Read an exact bounded page; follow next_offset for more.",
        "inputSchema": {
            "type": "object",
            "properties": {"event": {"type": "integer"}, "offset": {"type": "integer"}},
            "required": ["event"],
        },
    },
    {
        "name": "memory_checkpoint",
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        "description": "Save a durable next-turn checkpoint. Does not clear this native chat.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def call(archive, chat, name, args, jev=False):
    if name == "memory_status":
        return archive.stats(chat)
    if name == "memory_zoom":
        return archive.zoom(chat, args["event"], args.get("offset", 0))
    if name == "memory_search":
        from .retrieval import search

        return search(archive, chat, args["query"], use_jev=jev)
    if name == "memory_checkpoint":
        checkpoint_id, packet = checkpoint(archive, chat)
        return {"checkpoint": checkpoint_id, **packet.metadata(), "cleared": False}
    raise ValueError("Unknown tool")


def serve(archive, chat, delegate_backend=None, model=None, jev=False, connectome=None):
    for stream in (sys.stdin, sys.stdout):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    def trace(event):
        path = os.environ.get("MEMORY_TOOL_DIAGNOSTICS")
        if path:
            with Path(path).open("a", encoding="utf-8") as log:
                log.write(json.dumps({"time": time.time(), "event": event}) + "\n")

    archive.project(chat)
    tools = list(TOOLS)
    worker_calls = 0
    if delegate_backend:
        tools.append(
            {
                "name": "delegate",
                "description": "Ask a read-only Claude worker to inspect project files. Include the relevant task requirements.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"task": {"type": "string"}},
                    "required": ["task"],
                },
            }
        )
    for line in sys.stdin:
        request = json.loads(line)
        if "id" not in request:
            continue
        try:
            method = request["method"]
            if method == "initialize":
                result = {
                    "protocolVersion": request["params"]["protocolVersion"],
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "memory-tool", "version": "0.1.0"},
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": tools}
            elif method == "tools/call":
                params = request["params"]
                trace("call_started:" + params["name"])
                try:
                    if connectome:
                        archive.import_connectome(
                            chat, connectome, archive.project(chat)
                        )
                    trace("archive_refreshed")
                    if params["name"] == "delegate" and delegate_backend:
                        worker_calls += 1
                        if worker_calls > 4:
                            raise ValueError(
                                "Four worker calls per MCP session maximum"
                            )
                        from .claude import delegate_readonly

                        value = delegate_readonly(
                            archive, chat, params["arguments"]["task"], model
                        )
                    else:
                        value = call(
                            archive,
                            chat,
                            params["name"],
                            params.get("arguments", {}),
                            jev,
                        )
                    trace("tool_complete")
                    result = {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(value, ensure_ascii=False),
                            }
                        ]
                    }
                except Exception as error:
                    trace("tool_failed:" + type(error).__name__)
                    result = {
                        "isError": True,
                        "content": [{"type": "text", "text": str(error)}],
                    }
            else:
                raise ValueError("Unsupported method")
            response = {"jsonrpc": "2.0", "id": request["id"], "result": result}
        except Exception as error:
            response = {
                "jsonrpc": "2.0",
                "id": request["id"],
                "error": {"code": -32601, "message": str(error)},
            }
        print(json.dumps(response, ensure_ascii=True), flush=True)
        trace("response_sent")
