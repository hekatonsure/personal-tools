"""Loopback-only HTTP pass-through with independent, bounded public capture.

Optional archived-tool-result compaction; no credential discovery or retries.
Response entity bytes are preserved; hop-by-hop framing is rebuilt.
"""

import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import socket
from urllib.parse import urlsplit

import httpx

from .proxy_capture import Capture


HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


def forwarded_headers(headers, request=False):
    pairs = list(headers)
    excluded = HOP_HEADERS | (
        {"host", "content-length", "expect"} if request else set()
    )
    for name, value in pairs:
        if name.lower() == "connection":
            excluded |= {part.strip().lower() for part in value.split(",")}
    return [(name, value) for name, value in pairs if name.lower() not in excluded]


def upstream_url(value):
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Upstream must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Upstream URL cannot contain credentials, query, or fragment")
    if parsed.scheme == "http":
        try:
            local = ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            local = False
        if not local:
            raise ValueError("Plain HTTP upstreams must use a literal loopback address")
    return value.rstrip("/")


class ProxyServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 16

    def __init__(
        self,
        port,
        upstream,
        provider,
        db,
        chat,
        session,
        *,
        timeout=120,
        max_request=32 * 1024 * 1024,
        max_capture=8 * 1024 * 1024,
        compact_at=None,
        keep_turns=3,
        result_chars=500,
        summary_tree=False,
        summary_model=None,
        summary_lines=32,
        summary_calls=8,
        summary_seconds=30,
        codex_home=None,
    ):
        if provider not in {"anthropic", "openai"}:
            raise ValueError("Unknown proxy provider")
        if not session or ":" in session:
            raise ValueError("Use a nonempty session ID without colons")
        if timeout <= 0 or max_request <= 0 or max_capture <= 0:
            raise ValueError("Proxy limits must be positive")
        self.upstream = upstream_url(upstream)
        self.provider, self.db, self.chat, self.session = provider, db, chat, session
        self.timeout_seconds = timeout
        self.max_request, self.max_capture = max_request, max_capture
        self.compactor = None
        self.summary_tree = summary_tree
        self.compact_at = compact_at
        if (summary_tree and compact_at is None) or (
            summary_model and not summary_tree
        ):
            raise ValueError(
                "Summary tree requires --compact-at; summary model requires --summary-tree"
            )

        def compactor(chat, session):
            if compact_at is None:
                return None
            from .compaction import Compactor

            if summary_tree:
                from .summarizer import CodexSummarizer
                from .tree_compaction import TreeCompactor

                return TreeCompactor(
                    db,
                    chat,
                    session,
                    compact_at,
                    keep_turns,
                    max_lines=summary_lines,
                    max_calls=summary_calls,
                    seconds=summary_seconds,
                    summarizer=CodexSummarizer(summary_model)
                    if summary_model
                    else None,
                )
            return Compactor(compact_at, keep_turns, result_chars)

        self.compactor = compactor(chat, session)
        self.router = None
        if codex_home is not None:
            if provider != "openai":
                raise ValueError("Native Codex routing requires the OpenAI provider")
            from .proxy_routing import CodexRouter

            self.router = CodexRouter(db, codex_home, compactor)
        super().__init__(("127.0.0.1", port), ProxyHandler)

    def handle_error(self, request, client_address):
        # The default server traceback can contain request/auth data.
        pass


class ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def setup(self):
        super().setup()
        self.connection.settimeout(self.server.timeout_seconds)

    def log_message(self, *_):
        pass  # Never log URLs, headers, bodies, or authorization.

    def error(self, status, message):
        body = json.dumps(
            {"error": {"type": "memory_proxy_error", "message": message}}
        ).encode()
        self.send_response_only(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def do_GET(self):
        port = self.server.server_port
        if (
            self.path != "/health"
            or self.headers.get_all("Host", [])
            not in ([f"127.0.0.1:{port}"], [f"localhost:{port}"])
            or self.headers.get("Origin")
        ):
            self.error(404, "Unsupported proxy endpoint")
            return
        body = json.dumps(
            {
                "ok": True,
                "summary_tree": self.server.summary_tree,
                "compact_at": self.server.compact_at,
                "codex_routing": self.server.router is not None,
                **(self.server.router.status() if self.server.router else {}),
            }
        ).encode()
        self.send_response_only(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        # A fixed origin plus strict local Host prevents browser/DNS-rebinding use.
        hosts = self.headers.get_all("Host", [])
        port = self.server.server_port
        if (
            len(hosts) != 1
            or hosts[0] not in {f"127.0.0.1:{port}", f"localhost:{port}"}
            or self.headers.get("Origin")
        ):
            self.error(403, "Only local non-browser clients are accepted")
            return
        target = urlsplit(self.path)
        allowed = (
            {"/v1/messages", "/v1/messages/count_tokens"}
            if self.server.provider == "anthropic"
            else {
                "/responses",
                "/v1/responses",
                "/responses/compact",
                "/v1/responses/compact",
            }
        )
        if (
            target.scheme
            or target.netloc
            or target.fragment
            or target.path not in allowed
        ):
            self.error(404, "Unsupported proxy endpoint")
            return
        lengths = self.headers.get_all("Content-Length", [])
        if (
            self.headers.get("Transfer-Encoding")
            or len(lengths) != 1
            or not lengths[0].isdigit()
        ):
            self.error(
                411,
                "Exactly one Content-Length is required; chunked requests are unsupported",
            )
            return
        size = int(lengths[0])
        if size > self.server.max_request:
            self.error(413, "Request exceeds proxy size limit")
            return
        try:
            body = self.rfile.read(size)
        except (OSError, socket.timeout):
            self.error(408, "Request body timed out")
            return
        if len(body) != size:
            self.error(400, "Incomplete request body")
            return
        capture = None
        compactor = self.server.compactor
        chat, session = self.server.chat, self.server.session
        exchange = not target.path.endswith(("/count_tokens", "/compact"))
        if exchange and self.server.router:
            route = self.server.router.resolve(
                self.headers, body, self.headers.get("Content-Encoding", "identity")
            )
            if route is None:
                exchange = (
                    False  # Forward unknown identities without storing/mixing history.
                )
            else:
                chat, session, compactor = route
        # Provider-native compaction is forwarded verbatim, without public capture.
        if exchange:
            capture = Capture(
                self.server.db,
                chat,
                session,
                self.server.provider,
                body,
                self.headers.get("Content-Encoding", "identity"),
            )
        forwarded = body
        if capture and compactor:
            # Signed HTTP envelopes cannot be rewritten without re-signing.
            integrity = {
                "content-md5",
                "digest",
                "content-digest",
                "signature",
                "signature-input",
                "x-amz-content-sha256",
            }
            if any(
                name.lower() in integrity for name in self.headers
            ) or self.headers.get("Authorization", "").startswith("AWS4-"):
                detail = {"reason": "signed_request", "applied": False}
            else:
                forwarded, detail = compactor.rewrite(
                    body,
                    self.server.provider,
                    capture.input_events
                    if self.server.summary_tree
                    else capture.result_sources,
                    encoding=self.headers.get("Content-Encoding", "identity"),
                    archive_ok=not capture.input_failed
                    and capture.detail["input_capture"] == "recorded",
                )
            capture.detail["compaction"] = detail
        if capture:
            capture.detail.update(
                forwarded_request_bytes=len(forwarded),
                forwarded_request_sha256=hashlib.sha256(forwarded).hexdigest(),
            )
        copied = bytearray()
        hasher = hashlib.sha256()
        count = 0
        status = None
        content_type = encoding = ""
        delivered = started = overflow = False
        self.close_connection = True
        try:
            # trust_env=False prevents ambient proxy variables from changing the
            # explicitly selected upstream. Authentication comes only from the client.
            with httpx.Client(
                trust_env=False,
                follow_redirects=False,
                timeout=httpx.Timeout(self.server.timeout_seconds, connect=10),
            ) as client:
                client.headers.clear()  # Do not add HTTPX's User-Agent/encoding defaults.
                headers = forwarded_headers(self.headers.items(), request=True)
                with client.stream(
                    "POST",
                    self.server.upstream + self.path,
                    headers=headers,
                    content=forwarded,
                ) as response:
                    status = response.status_code
                    content_type = response.headers.get("content-type", "")
                    encoding = response.headers.get("content-encoding", "identity")
                    self.send_response_only(status)
                    for name, value in forwarded_headers(
                        response.headers.multi_items()
                    ):
                        self.send_header(name, value)
                    self.send_header("Connection", "close")
                    self.end_headers()
                    started = True
                    # iter_raw preserves encoded bodies and emits SSE chunks as
                    # they arrive. No buffering until message completion or retries.
                    for chunk in response.iter_raw():
                        self.wfile.write(chunk)
                        self.wfile.flush()
                        count += len(chunk)
                        hasher.update(chunk)
                        if count <= self.server.max_capture:
                            copied.extend(chunk)
                        else:
                            copied.clear()
                            overflow = True
                    delivered = True
        except (httpx.HTTPError, OSError):
            if not started:
                try:
                    self.error(
                        502, "Upstream connection failed; request was not retried"
                    )
                except OSError:
                    pass
            # Mid-stream failures close the connection. Never append a fake terminal
            # event or retry a potentially billable request.
        finally:
            if capture:
                capture.finish(
                    bytes(copied),
                    status=status,
                    content_type=content_type,
                    encoding=encoding,
                    size=count,
                    sha256=hasher.hexdigest(),
                    delivered=delivered,
                    overflow=overflow,
                )


def serve_proxy(
    db,
    chat,
    session,
    provider,
    upstream,
    port=8787,
    *,
    compact_at=None,
    keep_turns=3,
    result_chars=500,
    summary_tree=False,
    summary_model=None,
    summary_lines=32,
    summary_calls=8,
    summary_seconds=30,
    codex_home=None,
):
    with ProxyServer(
        port,
        upstream,
        provider,
        db,
        chat,
        session,
        compact_at=compact_at,
        keep_turns=keep_turns,
        result_chars=result_chars,
        summary_tree=summary_tree,
        summary_model=summary_model,
        summary_lines=summary_lines,
        summary_calls=summary_calls,
        summary_seconds=summary_seconds,
        codex_home=codex_home,
    ) as server:
        print(
            json.dumps(
                {
                    "listening": f"http://127.0.0.1:{server.server_port}",
                    "mode": "summary-tree"
                    if summary_tree
                    else "tool-results"
                    if compact_at is not None
                    else "passthrough",
                    "compact_at": compact_at,
                    "provider": provider,
                    "chat": chat,
                    "session": session,
                    "codex_routing": codex_home is not None,
                }
            ),
            flush=True,
        )
        server.serve_forever()
