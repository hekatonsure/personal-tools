import json
import os
import shutil
import subprocess

from memory_tool.archive import Archive
from memory_tool.claude import archive_stream
from memory_tool.mcp import call
from memory_tool.retrieval import search


def test_claude_archives_public_blocks_without_hidden_reasoning(tmp_path):
    archive = Archive(tmp_path / "a.sqlite")
    archive.register("main", str(tmp_path))
    fixture = {
        "type": "assistant",
        "message": {
            "id": "m1",
            "content": [
                {"type": "thinking", "thinking": "hidden"},
                {"type": "text", "text": "public"},
                {
                    "type": "tool_use",
                    "id": "c1",
                    "name": "memory_zoom",
                    "input": {"event": 9},
                },
            ],
        },
    }
    archive_stream(archive, "main", "s", fixture)
    archive_stream(archive, "main", "s", fixture)
    archive_stream(
        archive,
        "main",
        "s",
        {
            "type": "user",
            "uuid": "u2",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "c1",
                        "content": "exact source",
                    }
                ]
            },
        },
    )
    events = archive.events("main")
    assert [e["role"] for e in events] == ["assistant", "tool_call", "tool_result"]
    assert all("hidden" not in e["raw"] for e in events)
    archive.close()


class Response:
    status_code = 200

    def json(self):
        return {"answers": {"q0": {"score": 3}}, "usage": {"input_tokens": 100}}


class Client:
    def __init__(self):
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return Response()


def test_jev_redacts_and_caches_exact_candidates(tmp_path):
    archive = Archive(tmp_path / "a.sqlite")
    archive.register("main", str(tmp_path))
    archive.append(
        "main", "user", "decision: <private>private value</private> token=abcdefghijk"
    )
    client = Client()
    first = search(
        archive, "main", "decision", use_jev=True, key="test-only", client=client
    )
    second = search(
        archive, "main", "decision", use_jev=True, key="test-only", client=client
    )
    assert first["mode"] == second["mode"] == "jev"
    assert first["usage"]["requests"] == 1 and second["usage"]["cached"] == 1
    sent = json.dumps(client.calls[0][1]["json"])
    assert "private value" not in sent and "abcdefghijk" not in sent
    assert first["hits"][0]["text"].startswith("decision:")
    assert first["usage"]["input_tokens"] == 100
    archive.close()


def test_jev_invalid_response_falls_back(tmp_path):
    archive = Archive(tmp_path / "a.sqlite")
    archive.register("main", str(tmp_path))
    archive.append("main", "user", "decision useful")

    class Broken(Client):
        def post(self, *args, **kwargs):
            class Invalid(Response):
                def json(self):
                    return {"answers": {"q0": {"score": 99}}}

            return Invalid()

    result = search(
        archive, "main", "decision", use_jev=True, key="test-only", client=Broken()
    )
    assert result["mode"] == "local_fallback" and result["hits"]
    archive.close()


def test_mcp_checkpoint_is_metadata_not_false_clear(tmp_path):
    archive = Archive(tmp_path / "a.sqlite")
    archive.register("main", str(tmp_path))
    archive.append("main", "user", "hello")
    result = call(archive, "main", "memory_checkpoint", {})
    assert result["cleared"] is False and "text" not in result
    archive.close()


def test_stdio_mcp_preserves_unicode_under_windows_pipe_encoding(tmp_path):
    path = tmp_path / "unicode.sqlite"
    archive = Archive(path)
    archive.register("main", str(tmp_path))
    event = archive.append("main", "user", "こんにちは🌍 שלום")
    archive.close()
    requests = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-03-26"},
        },
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "memory_zoom", "arguments": {"event": event}},
        },
    ]
    child_env = {**os.environ, "PYTHONIOENCODING": "cp1252"}
    result = subprocess.run(
        [
            shutil.which("uv"),
            "run",
            "--no-sync",
            "memory-tool",
            "--db",
            str(path),
            "serve",
            "--chat",
            "main",
        ],
        input="".join(json.dumps(r, ensure_ascii=False) + "\n" for r in requests),
        encoding="utf-8",
        env=child_env,
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    responses = [json.loads(line) for line in result.stdout.splitlines()]
    zoom = json.loads(responses[-1]["result"]["content"][0]["text"])
    assert zoom["text"] == "こんにちは🌍 שלום"
