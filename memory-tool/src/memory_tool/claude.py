"""Native Claude transcript capture and optional fresh print-session gateway."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid

from .archive import canonical, digest
from .codex import MASTER
from .packing import Tokens, checkpoint


def public_blocks(record):
    message = record.get("message", {})
    blocks = message.get("content", [])
    if isinstance(blocks, str):
        blocks = [{"type": "text", "text": blocks}]
    return [
        b
        for b in blocks
        if b.get("type") not in {"thinking", "redacted_thinking", "reasoning"}
    ]


def archive_stream(archive, chat, session, record):
    if record.get("type") not in {"user", "assistant"}:
        return []
    blocks = public_blocks(record)
    # Keep provenance, but never copy unfiltered message content or thinking fields.
    saved = {
        k: record[k]
        for k in (
            "type",
            "uuid",
            "sessionId",
            "cwd",
            "timestamp",
            "parentUuid",
            "isMeta",
            "isCompactSummary",
            "isSidechain",
        )
        if k in record
    }
    saved["message"] = {"content": blocks}
    for index, block in enumerate(blocks):
        kind = block.get("type")
        role = (
            "tool_call"
            if kind == "tool_use"
            else "tool_result"
            if kind == "tool_result"
            else record["type"]
        )
        text = (
            block.get("text", "")
            if kind == "text"
            else json.dumps(block, ensure_ascii=False)
        )
        if record.get("isCompactSummary") or (
            kind == "text"
            and "MEMORY-TOOL/v1" in text
            and text.removeprefix("<system-reminder>")
            .lstrip()
            .startswith("Memory restored for project ")
        ):
            role = "generated_memory"
        elif record.get("isMeta") and role == "user":
            role = "developer"
        identity = (
            record.get("uuid")
            or record.get("message", {}).get("id")
            or digest(json.dumps(saved))
        )
        archive.append(
            chat,
            role,
            text,
            f"claude:{session}:{identity}:{index}",
            json.dumps(saved, ensure_ascii=False),
            record.get("timestamp"),
        )
    return blocks


def capture_transcript(archive, event, max_bytes=64 * 1024 * 1024):
    """Capture native Claude JSONL locally; no Claude process or API is involved.

    Claude has no session_meta header and cwd can change on every message. Anchor
    the project to the first public record, then persist it across later hooks.
    UUIDs make replay safe when compaction rewrites/truncates a transcript.
    """
    session = event["session_id"]
    path = Path(event["transcript_path"]).resolve()
    key = canonical(str(path))
    archive.db.execute(
        "CREATE TABLE IF NOT EXISTS claude_cursors("
        "path TEXT PRIMARY KEY,session TEXT NOT NULL,project TEXT NOT NULL,"
        "offset INTEGER NOT NULL,anchor TEXT NOT NULL)"
    )
    prior = archive.db.execute(
        "SELECT * FROM claude_cursors WHERE path=?", (key,)
    ).fetchone()
    if prior and prior["session"] != session:
        raise ValueError("Transcript cursor identity changed")
    project = prior["project"] if prior else None
    offset = prior["offset"] if prior else 0
    observed = consumed = 0
    with path.open("rb") as stream:
        # Check the bytes at the cursor before trusting an append-only offset.
        # Claude may replace its transcript at compaction; rescan UUIDs in that case.
        stream.seek(max(0, offset - 256))
        anchor = stream.read(min(offset, 256)).hex()
        rescanned = bool(
            prior and (path.stat().st_size < offset or anchor != prior["anchor"])
        )
        if rescanned:
            offset = 0
        stream.seek(offset)
        while consumed < max_bytes:
            line = stream.readline(min(8 * 1024 * 1024, max_bytes - consumed))
            if not line or not line.endswith(b"\n"):
                break
            record = json.loads(line)
            if record.get("type") in {"user", "assistant"}:
                if record.get("sessionId") != session:
                    raise ValueError("Transcript session identity mismatch")
                cwd = record.get("cwd")
                if not isinstance(cwd, str) or not Path(cwd).is_absolute():
                    raise ValueError("Transcript record lacks absolute cwd")
                if (
                    not isinstance(record.get("timestamp"), str)
                    or not record["timestamp"]
                ):
                    raise ValueError("Public transcript record lacks stable timestamp")
                if not isinstance(record.get("uuid"), str) or not record["uuid"]:
                    raise ValueError("Public transcript record lacks stable UUID")
                if record.get("isSidechain"):
                    raise ValueError("Expected main Claude transcript, not a sidechain")
                project = project or canonical(cwd)
                chat = archive.chat_for_project(project)
                observed += len(archive_stream(archive, chat, session, record))
            offset += len(line)
            consumed += len(line)
        stream.seek(max(0, offset - 256))
        anchor = stream.read(min(offset, 256)).hex()
    # An empty startup transcript has no verified identity yet. Don't persist a
    # cursor until a public record establishes its project and session.
    if project:
        with archive.db:
            archive.db.execute(
                "INSERT INTO claude_cursors VALUES(?,?,?,?,?) ON CONFLICT(path) "
                "DO UPDATE SET offset=excluded.offset,anchor=excluded.anchor",
                (key, session, project, offset, anchor),
            )
    else:
        offset = 0
    return project or canonical(event["cwd"]), {
        "observed_public_records": observed,
        "offset": offset,
        "backlog_bytes": max(0, path.stat().st_size - offset),
        "rescanned": rescanned,
    }


def claude_command(model=None, tools="", mcp_config=None):
    executable = shutil.which("claude.exe" if sys.platform == "win32" else "claude")
    if not executable:
        raise FileNotFoundError("Install/authenticate Claude Code first")
    command = [
        executable,
        "-p",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--verbose",
        "--no-session-persistence",
        "--tools",
        tools,
        "--permission-mode",
        "dontAsk",
        "--disable-slash-commands",
        "--setting-sources",
        "",
        "--strict-mcp-config",
        "--system-prompt",
        MASTER
        if not tools
        else "You are a read-only delegated worker. Inspect only the supplied task. Return exact evidence and limits.",
    ]
    if model:
        command += ["--model", model]
    if mcp_config:
        command += [
            "--mcp-config",
            str(mcp_config),
            "--allowedTools",
            "mcp__memory__memory_search,mcp__memory__memory_zoom,mcp__memory__delegate",
        ]
    return command


def invoke(archive, chat, command, text, timeout=240):
    session = uuid.uuid4().hex
    payload = {
        "type": "user",
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    }
    result = subprocess.run(
        command,
        input=json.dumps(payload, ensure_ascii=False) + "\n",
        capture_output=True,
        encoding="utf-8",
        cwd=archive.project(chat),
        timeout=timeout,
    )
    final = None
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        archive_stream(archive, chat, session, record)
        if record.get("type") == "result":
            final = record
    if result.returncode or not final or final.get("is_error"):
        # Don't echo verbose stderr/authentication payloads into logs.
        message = str(
            (final or {}).get("result", "Claude failed before returning a final result")
        )[:500]
        raise RuntimeError(message)
    return final.get("result", "")


class ClaudeGateway:
    def __init__(self, archive, chat, model=None, budget=64000, recent=8000):
        self.archive, self.chat, self.model = archive, chat, model
        self.budget, self.recent = budget, recent

    def turn(self, prompt):
        if Tokens().count(prompt) > 8000:
            raise ValueError("User message exceeds 8000 tokens")
        checkpoint_id, packet = checkpoint(
            self.archive, self.chat, budget=self.budget, recent_budget=self.recent
        )
        operation = uuid.uuid4().hex
        self.archive.append(self.chat, "user", prompt)
        self.archive.operation(
            operation, self.chat, None, "archived", {"checkpoint": checkpoint_id}
        )
        try:
            with tempfile.TemporaryDirectory(prefix="memory-tool-claude-") as folder:
                config = Path(folder) / "mcp.json"
                config.write_text(
                    json.dumps(
                        {
                            "mcpServers": {
                                "memory": {
                                    "command": sys.executable,
                                    "args": [
                                        "-m",
                                        "memory_tool.cli",
                                        "--db",
                                        str(self.archive.path.resolve()),
                                        "serve",
                                        "--chat",
                                        self.chat,
                                        "--delegate-backend",
                                        "claude",
                                        *(
                                            ["--model", self.model]
                                            if self.model
                                            else []
                                        ),
                                    ],
                                }
                            }
                        }
                    ),
                    encoding="utf-8",
                )
                command = claude_command(self.model, mcp_config=config)
                answer = invoke(
                    self.archive,
                    self.chat,
                    command,
                    packet.text
                    + "\nEND HISTORICAL MEMORY\nCURRENT USER MESSAGE:\n"
                    + prompt,
                )
            if not answer:
                raise RuntimeError("Claude returned an empty answer")
            checkpoint(
                self.archive, self.chat, budget=self.budget, recent_budget=self.recent
            )
            self.archive.operation(
                operation, self.chat, None, "complete", {"checkpoint": checkpoint_id}
            )
            return answer
        except BaseException as error:
            self.archive.operation(
                operation,
                self.chat,
                None,
                "failed",
                {"checkpoint": checkpoint_id, "error": str(error)},
            )
            raise


def delegate_readonly(archive, chat, task, model=None):
    if Tokens().count(task) > 8000:
        raise ValueError("Worker task exceeds 8000 tokens")
    answer = invoke(archive, chat, claude_command(model, tools="Read,Glob,Grep"), task)
    event = archive.append(chat, "worker_result", answer)
    return {
        "event": event,
        "text": Tokens().fit(answer, 3000),
        "truncated": Tokens().count(answer) > 3000,
    }
