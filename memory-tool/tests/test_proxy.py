"""Local HTTP fixtures only: no provider processes, credentials, or paid calls."""

from contextlib import contextmanager
import gzip
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
import threading
import time

import httpx
import pytest

from memory_tool.archive import Archive
from memory_tool.cli import parser
from memory_tool.proxy import ProxyServer, upstream_url
from memory_tool.proxy_capture import Capture, response_items
from memory_tool.selection import session_of


def sse(value):
    return b"data: " + json.dumps(value, ensure_ascii=False).encode() + b"\n\n"


def anthropic_stream():
    return b"".join(
        map(
            sse,
            [
                {
                    "type": "message_start",
                    "message": {"id": "msg-fixture", "content": []},
                },
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "thinking", "thinking": ""},
                },
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "thinking_delta", "thinking": "PRIVATE_THOUGHT"},
                },
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {
                        "type": "signature_delta",
                        "signature": "PRIVATE_SIGNATURE",
                    },
                },
                {"type": "content_block_stop", "index": 0},
                {
                    "type": "content_block_start",
                    "index": 1,
                    "content_block": {"type": "text", "text": ""},
                },
                {
                    "type": "content_block_delta",
                    "index": 1,
                    "delta": {"type": "text_delta", "text": "Visible Ω."},
                },
                {"type": "content_block_stop", "index": 1},
                {
                    "type": "content_block_start",
                    "index": 2,
                    "content_block": {
                        "type": "tool_use",
                        "id": "t1",
                        "name": "Read",
                        "input": {},
                    },
                },
                {
                    "type": "content_block_delta",
                    "index": 2,
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": '{"file_path":',
                    },
                },
                {
                    "type": "content_block_delta",
                    "index": 2,
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": '"/example"}',
                    },
                },
                {"type": "content_block_stop", "index": 2},
                {"type": "message_delta", "delta": {"stop_reason": "tool_use"}},
                {"type": "message_stop"},
            ],
        )
    )


def response_output():
    return [
        {
            "type": "reasoning",
            "id": "r1",
            "encrypted_content": "PRIVATE_ENCRYPTED",
            "summary": [],
        },
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "Public answer."}],
        },
        {
            "type": "function_call",
            "id": "f1",
            "call_id": "c1",
            "name": "lookup",
            "arguments": '{"id":4}',
        },
    ]


@contextmanager
def running(server):
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


@pytest.fixture
def archive_path(tmp_path):
    path = tmp_path / "memory.sqlite"
    archive = Archive(path)
    archive.register("main", str(tmp_path))
    archive.close()
    return path


def snapshot(path):
    archive = Archive(path)
    try:
        return (
            archive.events("main"),
            [
                {**dict(row), "detail": json.loads(row["detail"])}
                for row in archive.db.execute("SELECT * FROM operations")
            ],
            "\n".join(archive.db.iterdump()),
        )
    finally:
        archive.close()


def completed(path, count=1):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        result = snapshot(path)
        if len(result[1]) >= count and all(
            r["state"] != "forwarding" for r in result[1]
        ):
            return result
        time.sleep(0.01)
    pytest.fail("proxy capture did not finish")


def upstream(
    body,
    content_type="application/json",
    status=200,
    extra_headers=(),
    gate=None,
    truncated=False,
):
    received = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            received.append(
                (
                    self.path,
                    dict(self.headers),
                    self.rfile.read(int(self.headers["Content-Length"])),
                )
            )
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header(
                "Content-Length", str(len(body) + (20 if truncated else 0))
            )
            self.send_header("Connection", "close, X-Hop-Only")
            self.send_header("X-Hop-Only", "drop-me")
            for k, v in extra_headers:
                self.send_header(k, v)
            self.end_headers()
            try:
                if gate:
                    first = body.index(b"\n\n") + 2
                    self.wfile.write(body[:first])
                    self.wfile.flush()
                    if not gate.wait(timeout=3):
                        return
                    self.wfile.write(body[first:])
                else:
                    # Split inside JSON and UTF-8 sequences to exercise transparent
                    # forwarding independently of SSE frame boundaries.
                    for start in range(0, len(body), 13):
                        self.wfile.write(body[start : start + 13])
                        self.wfile.flush()
            except OSError:
                pass
            self.close_connection = True

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    return server, received


def proxy(origin, path, provider="anthropic", **kwargs):
    return ProxyServer(0, origin, provider, path, "main", "session-one", **kwargs)


def post(url, body, path="/v1/messages", **headers):
    return httpx.post(
        url + path,
        content=body,
        headers={"Content-Type": "application/json", **headers},
        trust_env=False,
    )


def test_anthropic_bytes_headers_streaming_and_public_capture(archive_path):
    response = anthropic_stream()
    gate = threading.Event()
    server, received = upstream(
        response,
        "text/event-stream",
        extra_headers=[("request-id", "req-test")],
        gate=gate,
    )
    request = b'{ "model":"fixture", "stream":true, "messages":[{"role":"user","content":"remember violet"},{"role":"assistant","content":[{"type":"thinking","thinking":"PRIVATE_INPUT","signature":"PRIVATE_SIGNATURE"}]}] }'
    with running(server) as origin, running(proxy(origin, archive_path)) as url:
        with httpx.Client(trust_env=False, timeout=4) as client:
            with client.stream(
                "POST",
                url + "/v1/messages?beta=true",
                content=request,
                headers={
                    "Authorization": "Bearer FAKE_AUTH_SENTINEL",
                    "x-api-key": "FAKE_KEY_SENTINEL",
                    "anthropic-version": "2023-06-01",
                    "anthropic-beta": "test-beta",
                    "Connection": "keep-alive, X-Private-Hop",
                    "X-Private-Hop": "drop-me",
                },
            ) as result:
                iterator = result.iter_raw()
                first = next(iterator)
                assert (
                    first and not gate.is_set()
                )  # Bytes reached client before upstream finished.
                gate.set()
                assert first + b"".join(iterator) == response
                assert (
                    result.status_code == 200
                    and result.headers["request-id"] == "req-test"
                )
                assert "x-hop-only" not in result.headers
        rows, ops, dump = completed(archive_path)
    assert received[0][0] == "/v1/messages?beta=true" and received[0][2] == request
    sent = {k.lower(): v for k, v in received[0][1].items()}
    assert sent["authorization"] == "Bearer FAKE_AUTH_SENTINEL"
    assert (
        sent["x-api-key"] == "FAKE_KEY_SENTINEL"
        and sent["anthropic-beta"] == "test-beta"
    )
    assert "x-private-hop" not in sent
    assert [r["role"] for r in rows] == ["user", "assistant", "tool_call"]
    assert (
        rows[1]["text"] == "Visible Ω." and '"file_path":"/example"' in rows[2]["text"]
    )
    assert "PRIVATE_" not in dump and "FAKE_AUTH" not in dump and "FAKE_KEY" not in dump
    assert all(session_of(row) == "session-one" for row in rows)
    detail = ops[0]["detail"]
    assert detail["request_sha256"] == hashlib.sha256(request).hexdigest()
    assert detail["response_sha256"] == hashlib.sha256(response).hexdigest()
    assert detail["output_capture"] == "recorded" and detail["delivered"]


@pytest.mark.parametrize("streaming", [False, True])
def test_openai_responses_preserve_encrypted_reasoning_but_never_store_it(
    archive_path, streaming
):
    final = {"id": "resp-fixture", "status": "completed", "output": response_output()}
    response = (
        sse({"type": "response.completed", "response": final})
        if streaming
        else json.dumps(final).encode()
    )
    server, received = upstream(
        response, "text/event-stream" if streaming else "application/json"
    )
    request = json.dumps(
        {
            "input": [
                {"role": "user", "content": "Use the violet build."},
                {"type": "reasoning", "encrypted_content": "PRIVATE_INPUT"},
                {
                    "type": "function_call_output",
                    "call_id": "c0",
                    "output": "lookup worked",
                },
            ]
        }
    ).encode()
    with (
        running(server) as origin,
        running(proxy(origin + "/v1", archive_path, "openai")) as url,
    ):
        result = post(url, request, path="/responses", Authorization="Bearer FAKE_AUTH")
        assert result.content == response
        rows, ops, dump = completed(archive_path)
    assert received[0][0] == "/v1/responses" and received[0][2] == request
    assert [r["role"] for r in rows] == [
        "user",
        "tool_result",
        "assistant",
        "tool_call",
    ]
    assert "PRIVATE_" not in dump and "FAKE_AUTH" not in dump
    assert ops[0]["detail"]["output_capture"] == "recorded"


@pytest.mark.parametrize("status", [307, 401, 429, 500])
def test_upstream_errors_and_redirects_pass_through_without_retry(archive_path, status):
    body = b'{"error":"fixture error"}'
    server, received = upstream(
        body,
        status=status,
        extra_headers=[
            ("Retry-After", "12"),
            ("Location", "https://example.invalid/never-follow"),
        ],
    )
    with running(server) as origin, running(proxy(origin, archive_path)) as url:
        result = post(url, b'{"messages":[]}')
        assert result.status_code == status and result.content == body
        assert result.headers["retry-after"] == "12"
        rows, ops, _ = completed(archive_path)
    assert len(received) == 1 and not rows
    assert ops[0]["detail"]["output_capture"] == "not_completed"


def test_archive_failure_does_not_break_forwarding(archive_path, tmp_path, capsys):
    response = (
        b'{"type":"message","role":"assistant","content":[{"type":"text","text":"ok"}]}'
    )
    bad_db = tmp_path / "directory-not-database"
    bad_db.mkdir()
    server, _ = upstream(response)
    with running(server) as origin, running(proxy(origin, bad_db)) as url:
        result = post(url, b'{"messages":[]}')
        assert result.status_code == 200 and result.content == response
    assert "capture failed" in capsys.readouterr().err


@pytest.mark.parametrize("mode", ["oversized", "gzip", "malformed", "incomplete"])
def test_capture_limits_do_not_change_response_bytes(archive_path, mode):
    headers, limit = [], 8 * 1024 * 1024
    body = anthropic_stream()
    if mode == "oversized":
        limit = 30
    elif mode == "gzip":
        body = gzip.compress(body)
        headers = [("Content-Encoding", "gzip")]
    elif mode == "malformed":
        body = b"data: invalid json\n\n"
    else:
        body = body[: body.rfind(b"data:")]
    server, _ = upstream(body, "text/event-stream", extra_headers=headers)
    with (
        running(server) as origin,
        running(proxy(origin, archive_path, max_capture=limit)) as url,
    ):
        with httpx.Client(trust_env=False) as client:
            with client.stream(
                "POST", url + "/v1/messages", content=b'{"messages":[]}'
            ) as response:
                assert b"".join(response.iter_raw()) == body
        rows, ops, _ = completed(archive_path)
    expected = {
        "oversized": "size_limit",
        "gzip": "unsupported_encoding",
        "malformed": "parse_failed",
        "incomplete": "incomplete",
    }
    assert ops[0]["detail"]["output_capture"] == expected[mode]
    assert not rows


def test_truncated_stream_is_not_retried_or_marked_complete(archive_path):
    body = anthropic_stream()[:100]
    server, received = upstream(body, "text/event-stream", truncated=True)
    with running(server) as origin, running(proxy(origin, archive_path)) as url:
        with pytest.raises(httpx.RemoteProtocolError):
            post(url, b'{"messages":[]}')
        rows, ops, _ = completed(archive_path)
    assert len(received) == 1 and not rows
    assert ops[0]["state"] == "interrupted" and not ops[0]["detail"]["delivered"]


def test_connection_failure_returns_bounded_error(archive_path):
    with socket.socket() as closed:
        closed.bind(("127.0.0.1", 0))
        port = closed.getsockname()[1]
    with running(proxy(f"http://127.0.0.1:{port}", archive_path)) as url:
        result = post(url, b'{"messages":[]}')
        assert result.status_code == 502 and "not retried" in result.text
        _, ops, _ = completed(archive_path)
    assert ops[0]["state"] == "interrupted"


def test_rejects_browser_unknown_endpoint_and_oversized_request(archive_path):
    server, received = upstream(b"{}")
    with (
        running(server) as origin,
        running(proxy(origin, archive_path, max_request=20)) as url,
    ):
        assert post(url, b"{}", Origin="https://example.com").status_code == 403
        assert post(url, b"{}", Host="example.com").status_code == 403
        assert post(url, b"{}", path="/anything").status_code == 404
        assert post(url, b"x" * 21).status_code == 413
        with httpx.Client(trust_env=False) as client:
            assert (
                client.post(url + "/v1/messages", content=iter([b"{}"])).status_code
                == 411
            )
    assert not received


def test_count_tokens_passes_without_archiving(archive_path):
    server, received = upstream(b'{"input_tokens":123}')
    with running(server) as origin, running(proxy(origin, archive_path)) as url:
        assert post(
            url, b'{"messages":[]}', path="/v1/messages/count_tokens"
        ).json() == {"input_tokens": 123}
    assert len(received) == 1 and not snapshot(archive_path)[1]


def test_replayed_input_is_idempotent_but_repeated_turns_survive(archive_path):
    def save(messages, **extra):
        Capture(
            archive_path,
            "main",
            "session-one",
            "openai",
            json.dumps({"input": messages, **extra}).encode(),
        )

    first = [{"role": "user", "content": "again"}]
    save(first)
    save(first)
    assert len(snapshot(archive_path)[0]) == 1
    save([*first, {"role": "assistant", "content": "ok"}, *first])
    assert len(snapshot(archive_path)[0]) == 3
    save(first, previous_response_id="resp-before")
    assert len(snapshot(archive_path)[0]) == 4
    save([{"role": "user", "content": "Changed earlier instruction"}, *first])
    assert len(snapshot(archive_path)[0]) == 6


def test_invalid_request_capture_still_forwards_original_bytes(archive_path):
    server, received = upstream(b'{"error":"bad input"}', status=400)
    with running(server) as origin, running(proxy(origin, archive_path)) as url:
        assert post(url, b"invalid JSON").status_code == 400
        _, ops, _ = completed(archive_path)
    assert received[0][2] == b"invalid JSON"
    assert ops[0]["detail"]["input_capture"] == "failed"


def test_stream_error_never_becomes_completed_output():
    body = anthropic_stream() + sse({"type": "error", "error": {"message": "failed"}})
    assert response_items("anthropic", body, True)[1] is False
    openai = sse(
        {
            "type": "response.failed",
            "response": {"output": response_output(), "status": "failed"},
        }
    )
    assert response_items("openai", openai, True)[1] is False


def test_public_capture_nested_reasoning_and_nonstream_tools():
    body = json.dumps(
        {
            "type": "message",
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "PRIVATE_THOUGHT"},
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Read",
                    "input": {"path": "/example"},
                },
            ],
        }
    ).encode()
    items, complete = response_items("anthropic", body, False)
    assert complete and [role for role, _ in items] == ["tool_call"]
    assert "PRIVATE_" not in json.dumps(items)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "https://key:secret@example.com",
        "https://example.com?key=secret",
        "file:///tmp/anything",
    ],
)
def test_rejects_unsafe_upstream_urls(url):
    with pytest.raises(ValueError):
        upstream_url(url)


def test_proxy_cli_requires_explicit_provider_upstream_and_session():
    args = parser().parse_args(
        [
            "proxy",
            "--chat",
            "main",
            "--provider",
            "openai",
            "--upstream",
            "https://api.openai.com",
            "--session",
            "fixture",
        ]
    )
    assert args.port == 8787


def compactable_request(provider):
    items = []
    for index in range(3):
        items.append({"role": "user", "content": f"task {index}"})
        if provider == "anthropic":
            items += [
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": f"c{index}",
                            "name": "Read",
                            "input": {"file_path": "/example"},
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": f"c{index}",
                            "content": "long original output " * 1000,
                        }
                    ],
                },
            ]
        else:
            items += [
                {
                    "type": "function_call",
                    "call_id": f"c{index}",
                    "name": "Read",
                    "arguments": "{}",
                },
                {
                    "type": "function_call_output",
                    "call_id": f"c{index}",
                    "output": "long original output " * 1000,
                },
            ]
    return json.dumps(
        {"messages" if provider == "anthropic" else "input": items}, indent=2
    ).encode()


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_compacting_proxy_forwards_reduction_and_archives_original(
    archive_path, provider
):
    original = compactable_request(provider)
    response = (
        anthropic_stream()
        if provider == "anthropic"
        else sse(
            {
                "type": "response.completed",
                "response": {"status": "completed", "output": response_output()},
            }
        )
    )
    server, received = upstream(response, "text/event-stream")
    with (
        running(server) as origin,
        running(
            proxy(
                origin,
                archive_path,
                provider,
                compact_at=10000,
                keep_turns=1,
                result_chars=100,
            )
        ) as url,
    ):
        result = post(
            url,
            original,
            path="/v1/messages" if provider == "anthropic" else "/v1/responses",
        )
        assert result.content == response
        rows, ops, dump = completed(archive_path)
    sent = received[0][2]
    assert len(sent) < len(original) and b"memory_zoom event=" in sent
    assert sent[sent.index(b"task 2") :] == original[original.index(b"task 2") :]
    assert any("long original output " * 1000 in row["text"] for row in rows)
    detail = ops[0]["detail"]
    assert detail["request_sha256"] == hashlib.sha256(original).hexdigest()
    assert detail["forwarded_request_sha256"] == hashlib.sha256(sent).hexdigest()
    assert detail["compaction"]["applied"] and detail["compaction"]["target_met"]
    assert detail["forwarded_request_bytes"] == len(sent)


@pytest.mark.parametrize("reason", ["archive", "signed", "encoded", "default"])
def test_compaction_guards_forward_original_bytes(archive_path, tmp_path, reason):
    original = compactable_request("anthropic")
    db, headers = archive_path, {}
    options = {"compact_at": 10000, "keep_turns": 1, "result_chars": 100}
    if reason == "archive":
        db = tmp_path / "not-a-database"
        db.mkdir()
    elif reason == "signed":
        headers["Content-Digest"] = "test-integrity"
    elif reason == "encoded":
        headers["Content-Encoding"] = "gzip"
        original = gzip.compress(original)
    else:
        options = {}
    server, received = upstream(b'{"type":"message","role":"assistant","content":[]}')
    with running(server) as origin, running(proxy(origin, db, **options)) as url:
        assert post(url, original, **headers).status_code == 200
        if reason != "archive":
            completed(archive_path)
    assert received[0][2] == original


def test_streaming_request_with_missing_response_content_type_is_captured(archive_path):
    body = sse(
        {
            "type": "response.completed",
            "response": {"status": "completed", "output": response_output()},
        }
    )
    for requested_stream, expected in [
        (True, "recorded"),
        (False, "unsupported_content_type"),
    ]:
        capture = Capture(
            archive_path,
            "main",
            "session-one",
            "openai",
            json.dumps({"stream": requested_stream, "input": "fixture"}).encode(),
        )
        capture.finish(
            body,
            status=200,
            content_type="",
            encoding="identity",
            size=len(body),
            sha256=hashlib.sha256(body).hexdigest(),
            delivered=True,
        )
        assert capture.detail["output_capture"] == expected
    assert "PRIVATE_ENCRYPTED" not in snapshot(archive_path)[2]


def test_missing_content_type_does_not_make_incomplete_stream_complete(archive_path):
    capture = Capture(
        archive_path,
        "main",
        "session-one",
        "openai",
        b'{"stream":true,"input":"fixture"}',
    )
    capture.finish(
        sse({"type": "response.created"}),
        status=200,
        content_type="",
        encoding="identity",
        size=1,
        sha256="fixture",
        delivered=True,
    )
    assert capture.detail["output_capture"] == "incomplete"
