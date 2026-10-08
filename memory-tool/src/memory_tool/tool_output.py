"""Readable search views of known tool transports. Stored events never change."""

import ast
import json
import re


VIEW_VERSION = 1


def _literal_echo(line):
    """Recognize printed Python search hits; never evaluate arbitrary code."""
    stripped = line.strip()
    if not stripped.startswith(("{'event':", '{"event":')):
        return False
    if len(stripped) > 100_000:
        return False
    try:
        return _echo(ast.literal_eval(stripped))
    except (ValueError, SyntaxError, RecursionError):
        return False


def _query_replays(text):
    """A batch of query + (event ID, excerpt) debug rows, not one data tuple."""
    found = set()
    for line in text.splitlines():
        match = re.fullmatch(r"[^\[\n]+ (\[\(\d+, .+\)\])", line)
        if not match or len(line) > 100_000:
            continue
        try:
            rows = ast.literal_eval(match[1])
        except (ValueError, SyntaxError, RecursionError):
            continue
        if len(rows) >= 2 and all(
            isinstance(row, tuple)
            and len(row) == 2
            and isinstance(row[0], int)
            and isinstance(row[1], str)
            for row in rows
        ):
            found.add(line)
    return found if len(found) >= 2 else set()


def _echo(value):
    if isinstance(value, list) and value:
        return all(
            isinstance(v, dict) and {"id", "source", "resume", "hits"} <= set(v)
            for v in value
        )
    if not isinstance(value, dict):
        return False
    keys = set(value)
    return (
        {"hits", "evidence"} <= keys
        and bool(keys & {"candidates", "usage"})
        or {"next_offset", "event", "text"} <= keys
        or {"event", "offset", "score", "text"} <= keys
        or {"source_events", "agent_feedback", "native_recovery"} <= keys
        or {"project", "session", "topic_event", "topic_excerpt"} <= keys
        or {"now", "facts", "hypotheses", "stance", "pins", "todo"} == keys
    )


def _parts(value, depth=0):
    # Unknown shapes stay visible. Bound decoding even for adversarial nesting.
    if depth >= 16:
        return [value if isinstance(value, str) else json.dumps(value)]
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (ValueError, RecursionError):
            # functions.exec prefixes its output with this transport-only block.
            if re.fullmatch(r"Script completed\nWall time [^\n]+\nOutput:\n?", value):
                return []
            if value.startswith(("MEMORY-TOOL/v1", "Memory restored for project ")):
                return []
            if (
                len(
                    re.findall(
                        r"(?m)^(?:EVENT )?\d+ (?:user|assistant|tool_result|tool_call) (?:len )?\d+(?:$| [\[{])",
                        value,
                    )
                )
                >= 2
            ):
                return []  # explicit raw-event debug replay, not fresh evidence
            # CLI search stdout may follow timing/progress lines. Decode a whole
            # structured block before classifying, including pretty-printed JSON.
            for match in re.finditer(r"(?m)^[\[{]", value):
                try:
                    block, end = json.JSONDecoder().raw_decode(value, match.start())
                except (ValueError, RecursionError):
                    continue
                if _echo(block):
                    return _parts(value[: match.start()] + value[end:], depth + 1)
            # Startup probes print one reference per line, sometimes beside new
            # diagnostics. Remove references only, preserving those diagnostics.
            lines = []
            # These probes print decoded archive excerpts after an explicit
            # raw/view-length header. Their nested command successes are copies.
            # A traceback starts a fresh diagnostic even in the same output leaf.
            diagnostic_copy = False
            query_replays = _query_replays(value)
            replay_rows = bool(re.search(r"(?m)^QUERY .+ hits \d+$", value)) and bool(
                re.search(
                    r"(?m)^\d+ (?:user|assistant|tool_result|tool_call) duplicates \d+ ",
                    value,
                )
            )
            continuation = False
            for line in value.splitlines(keepends=True):
                if line.rstrip("\r\n") in query_replays:
                    continue
                if re.match(
                    r"^\d+ raw chars \d+ view chars \d+ startup refs remaining (?:True|False)\s*$",
                    line,
                ):
                    diagnostic_copy = True
                    continue
                if diagnostic_copy:
                    if re.match(
                        r"^(?:Traceback \(most recent call last\):|[\w.]*(?:Error|Exception):|ERROR\b|FAILED\b)",
                        line,
                    ):
                        diagnostic_copy = False
                    else:
                        continue
                if _literal_echo(line):
                    continue
                if replay_rows:
                    if re.match(
                        r"^(?:QUERY .+ hits \d+$|\d+ (?:user|assistant|tool_result|tool_call) duplicates \d+ )",
                        line,
                    ):
                        continuation = True
                        continue
                    if continuation and (line[:1].isspace() or not line.strip()):
                        continue
                    continuation = False
                if re.match(r"^startup \d+ ", line) and all(
                    key in line
                    for key in (
                        "'repository'",
                        "'repository_sessions'",
                        "'recent_session_references'",
                    )
                ):
                    continue
                reference = None
                if line.lstrip().startswith(("{", "[")):
                    try:
                        reference = json.loads(line)
                    except (ValueError, RecursionError):
                        pass
                if not _echo(reference):
                    if isinstance(reference, dict) and (
                        {"status", "value"} <= set(reference)
                        or {"chunk_id", "output"} <= set(reference)
                    ):
                        lines.extend(_parts(reference, depth + 1))
                    else:
                        lines.append(line)
            return ["".join(lines)] if lines else []
        return _parts(decoded, depth + 1)
    if _echo(value):
        return []
    if (
        isinstance(value, list)
        and value
        and all(
            isinstance(v, dict)
            and v.get("type") in {"text", "input_text", "output_text"}
            and isinstance(v.get("text"), str)
            and set(v) <= {"type", "text", "annotations"}
            for v in value
        )
    ):
        return [part for v in value for part in _parts(v["text"], depth + 1)]
    if isinstance(value, dict):
        keys = set(value)
        if value.get("type") in {
            "function_call_output",
            "custom_tool_call_output",
        } and keys <= {"type", "call_id", "id", "output"}:
            return _parts(value.get("output", ""), depth + 1)
        if keys <= {"i", "result"} and "result" in keys:
            return _parts(value["result"], depth + 1)
        if keys <= {"i", "status", "value"} and value.get("status") == "fulfilled":
            return _parts(value.get("value", ""), depth + 1)
        if keys <= {"content", "isError"} and "content" in keys:
            parts = _parts(value["content"], depth + 1)
            return (["isError: true"] if value.get("isError") else []) + parts
        if keys == {"text"} or keys == {"value"}:
            return _parts(next(iter(value.values())), depth + 1)
        if "output" in keys and keys <= {
            "output",
            "chunk_id",
            "wall_time_seconds",
            "exit_code",
            "original_token_count",
            "session_id",
        }:
            parts = _parts(value["output"], depth + 1)
            # An echo's successful transport is not new source evidence. Failed
            # operations remain evidence even when their payload was an echo.
            if not parts and (
                value.get("exit_code") is None or value.get("exit_code") == 0
            ):
                return []
            metadata = [
                f"{k}: {value[k]}" for k in ("exit_code", "session_id") if k in value
            ]
            return metadata + parts
    return [json.dumps(value, ensure_ascii=False, indent=2)]


def searchable_output(text):
    """Decode transport wrappers and omit recognized copied evidence, not prose."""
    return "\n".join(_parts(text))


def excerpt(text, words, width=1024):
    """Choose a bounded literal window of the decoded view using query coverage."""
    folded = text.casefold()
    positions = [
        m.start() for w in words for m in re.finditer(re.escape(w.casefold()), folded)
    ]
    if not positions:
        return None
    # At most one candidate per window, even for huge repeated command output.
    starts = sorted({max(0, p - width // 4) // 256 * 256 for p in positions})
    start = max(
        starts, key=lambda i: sum(w.casefold() in folded[i : i + width] for w in words)
    )
    return text[start : start + width]
