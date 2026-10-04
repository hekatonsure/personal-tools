"""Small newline JSON-RPC client. No credentials, shell interpolation or daemon changes."""

from __future__ import annotations

import collections
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time


class RpcError(RuntimeError):
    pass


def codex_executable() -> str:
    explicit = os.environ.get("MEMORY_TOOL_CODEX")
    if explicit:
        if not Path(explicit).is_file():
            raise FileNotFoundError(explicit)
        return explicit
    if os.name == "nt":
        base = (
            Path(os.environ.get("APPDATA", ""))
            / "npm/node_modules/@openai/codex/node_modules"
        )
        matches = list(base.glob("@openai/codex-win32-*/vendor/*/bin/codex.exe"))
        if matches:
            return str(matches[0])
    found = shutil.which("codex.exe" if os.name == "nt" else "codex")
    if not found:
        raise FileNotFoundError(
            "Install/authenticate Codex first, or set MEMORY_TOOL_CODEX"
        )
    return found


class CodexRpc:
    def __init__(self, proxy=False, timeout=120, executable=None, on_message=None):
        command = [executable or codex_executable(), "app-server"]
        command += ["proxy"] if proxy else ["--stdio"]
        self.process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self.queue = queue.Queue()
        self.pending = collections.deque()
        self.stderr = collections.deque(maxlen=16)
        self.serial = 0
        self.timeout = timeout
        self.on_message = on_message
        threading.Thread(target=self._stdout, daemon=True).start()
        threading.Thread(target=self._stderr, daemon=True).start()
        try:
            self.request(
                "initialize",
                {
                    "clientInfo": {"name": "memory-tool", "version": "0.1.0"},
                    "capabilities": {"experimentalApi": True},
                },
            )
            self.send({"method": "initialized", "params": {}})
        except BaseException:
            self.close()
            raise

    def _stdout(self):
        try:
            for line in self.process.stdout:
                try:
                    self.queue.put(json.loads(line))
                except json.JSONDecodeError:
                    self.queue.put(RpcError("Non-JSON app-server output"))
        finally:
            self.queue.put(RpcError("App-server connection closed"))

    def _stderr(self):
        for line in self.process.stderr:
            self.stderr.append(line[:500])

    def send(self, payload):
        self.process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.process.stdin.flush()

    def _receive(self, timeout):
        try:
            message = self.queue.get(timeout=max(0.001, timeout))
        except queue.Empty as error:
            raise TimeoutError("App-server response timed out") from error
        if isinstance(message, BaseException):
            raise message
        if self.on_message:
            self.on_message(message)
        return message

    def next_event(self, timeout=None):
        if self.pending:
            return self.pending.popleft()
        return self._receive(self.timeout if timeout is None else timeout)

    def request(self, method, params):
        self.serial += 1
        request_id = self.serial
        self.send({"id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + self.timeout
        while True:
            message = self._receive(deadline - time.monotonic())
            if message.get("id") == request_id and "method" not in message:
                if "error" in message:
                    raise RpcError(f"{method}: {message['error']}")
                return message.get("result", {})
            self.pending.append(message)

    def reply_tool(self, request_id, text, success=True):
        self.send(
            {
                "id": request_id,
                "result": {
                    "contentItems": [{"type": "inputText", "text": text}],
                    "success": success,
                },
            }
        )

    def reject(self, request_id):
        self.send(
            {
                "id": request_id,
                "error": {
                    "code": -32601,
                    "message": "memory-tool does not grant approval or accept this request",
                },
            }
        )

    def close(self):
        if self.process.poll() is None:
            # EOF lets the server release worker processes, file handles and sandbox grants.
            if self.process.stdin and not self.process.stdin.closed:
                self.process.stdin.close()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            if stream:
                stream.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
