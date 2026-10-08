"""Deterministic, opt-in reduction of archived older tool-result text.

Only JSON string values are patched. All other request bytes survive, including
reasoning signatures, encrypted items, user text, recent turns and tool schemas.
"""

import hashlib
import json
import math
import threading

from .packing import Tokens
from .proxy_capture import encoded


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def json_spans(source, paths):
    """Locate exact JSON values, rather than reserializing the surrounding body."""
    decoder = json.JSONDecoder()
    spans = {}

    def space(index):
        while index < len(source) and source[index] in " \n\r\t":
            index += 1
        return index

    def walk(index, path, depth=0):
        if depth > 64:
            raise ValueError("JSON nesting limit")
        index = space(index)
        start = index
        char = source[index]
        if char == "{":
            index = space(index + 1)
            while source[index] != "}":
                key, index = decoder.raw_decode(source, index)
                index = space(index)
                if source[index] != ":":
                    raise ValueError("Expected JSON colon")
                index = space(walk(index + 1, (*path, key), depth + 1))
                if source[index] == "}":
                    break
                if source[index] != ",":
                    raise ValueError("Expected JSON comma")
                index = space(index + 1)
            index += 1
        elif char == "[":
            index, item = space(index + 1), 0
            while source[index] != "]":
                index = space(walk(index, (*path, item), depth + 1))
                item += 1
                if source[index] == "]":
                    break
                if source[index] != ",":
                    raise ValueError("Expected JSON comma")
                index = space(index + 1)
            index += 1
        else:
            _, index = decoder.raw_decode(source, index)
        if path in paths:
            spans[path] = (start, index)
        return index

    end = walk(0, ())
    if space(end) != len(source) or len(spans) != len(paths):
        raise ValueError("Incomplete JSON patch")
    return spans


def patch_strings(source, replacements):
    spans = json_spans(source, replacements)
    edits = []
    for path, replacement in replacements.items():
        start, end = spans[path]
        if source[start] != '"':
            raise ValueError("Compaction target must be a JSON string")
        edits.append((start, end, json.dumps(replacement, ensure_ascii=False)))
    for start, end, replacement in sorted(edits, reverse=True):
        source = source[:start] + replacement + source[end:]
    return source


def user_turn(item, provider):
    if item.get("role") != "user":
        return False
    content = item.get("content", "")
    if isinstance(content, str):
        return bool(content)
    # Anthropic delivers tool results as user messages; those aren't new tasks.
    return any(block.get("type") != "tool_result" for block in content)


def result_texts(items, provider, cutoff):
    """Yield text paths only for results with a prior, unique matching tool call."""
    calls, duplicates = set(), set()
    for index, item in enumerate(items[:cutoff]):
        if provider == "anthropic":
            content = item.get("content", [])
            if not isinstance(content, list):
                continue
            for block_index, block in enumerate(content):
                kind = block.get("type")
                identity = (
                    block.get("id") if kind == "tool_use" else block.get("tool_use_id")
                )
                if kind == "tool_use" and item.get("role") == "assistant":
                    if identity in calls:
                        duplicates.add(identity)
                    calls.add(identity)
                elif kind == "tool_result" and item.get("role") == "user":
                    if (
                        not isinstance(identity, str)
                        or identity not in calls
                        or identity in duplicates
                    ):
                        continue
                    path = ("messages", index, "content", block_index, "content")
                    yield from text_values(block.get("content", []), path, identity)
        else:
            kind, identity = item.get("type"), item.get("call_id")
            if kind in {"function_call", "custom_tool_call"}:
                if identity in calls:
                    duplicates.add(identity)
                calls.add(identity)
            elif kind in {"function_call_output", "custom_tool_call_output"}:
                if (
                    not isinstance(identity, str)
                    or identity not in calls
                    or identity in duplicates
                ):
                    continue
                yield from text_values(
                    item.get("output", ""), ("input", index, "output"), identity
                )


def text_values(value, path, identity):
    if isinstance(value, str):
        yield path, value, identity
    elif isinstance(value, list):
        for index, block in enumerate(value):
            if block.get("type") in {"text", "input_text", "output_text"}:
                if isinstance(block.get("text"), str):
                    yield (*path, index, "text"), block["text"], identity


class Compactor:
    def __init__(self, threshold=128000, keep_turns=3, result_chars=500):
        if threshold < 1 or keep_turns < 1 or result_chars < 1:
            raise ValueError(
                "Compaction threshold, keep-turns and result-chars must be positive"
            )
        self.threshold, self.keep_turns, self.result_chars = (
            threshold,
            keep_turns,
            result_chars,
        )
        self.cutoff, self.prefix = 0, None
        self.lock = threading.Lock()
        self.tokens = Tokens()

    def estimate(self, body):
        # This is a local estimate, not the provider's tokenizer or a hard cap.
        return max(self.tokens.count(body.decode("utf-8")), math.ceil(len(body) / 4))

    def rewrite(self, body, provider, sources, *, encoding="identity", archive_ok=True):
        if not archive_ok:
            return body, {"reason": "archive_unavailable", "applied": False}
        if encoding not in {"", "identity"}:
            return body, {"reason": "encoded_request", "applied": False}
        try:
            with self.lock:
                return self._rewrite(body, provider, sources)
        except Exception as error:
            # No exception text or payload is persisted in compaction diagnostics.
            return body, {
                "reason": "rewrite_failed",
                "error_type": type(error).__name__,
                "applied": False,
            }

    def _rewrite(self, body, provider, sources):
        source = body.decode("utf-8")
        value = json.loads(source, object_pairs_hook=unique_object)
        if any(
            value.get(key)
            for key in ("previous_response_id", "conversation", "context_management")
        ):
            return body, {"reason": "provider_managed_context", "applied": False}
        items = value.get("messages" if provider == "anthropic" else "input")
        if not isinstance(items, list):
            return body, {"reason": "unsupported_input", "applied": False}
        turns = [i for i, item in enumerate(items) if user_turn(item, provider)]
        eligible = turns[-self.keep_turns] if len(turns) > self.keep_turns else 0
        before = self.estimate(body)
        detail = {
            "applied": False,
            "estimated_before": before,
            "estimated_after": before,
            "threshold": self.threshold,
            "encoding": "o200k_base+utf8/4",
            "target_met": before <= self.threshold,
        }
        fingerprint = lambda end: hashlib.sha256(
            encoded(items[:end]).encode()
        ).hexdigest()
        reusable = self.cutoff <= eligible and self.prefix == fingerprint(self.cutoff)
        cutoff = self.cutoff if reusable else 0

        def build(end):
            replacements = {}
            for path, text, identity in result_texts(items, provider, end):
                event = sources.get(identity)
                if not isinstance(event, int) or isinstance(event, bool):
                    continue
                marker = f"\n[memory-tool excerpt: {max(0, len(text) - self.result_chars)} characters omitted; original: memory_zoom event={event}]\n"
                if len(text) <= self.result_chars + len(marker):
                    continue
                head = self.result_chars * 7 // 10
                tail = self.result_chars - head
                shortened = text[:head] + marker + text[-tail:]
                if len(shortened.encode()) >= len(text.encode()):
                    continue
                replacements[path] = shortened
            if not replacements:
                return body, 0
            return patch_strings(source, replacements).encode("utf-8"), len(
                replacements
            )

        candidate, changed = build(cutoff)
        after = self.estimate(candidate) if changed else before
        if after > self.threshold and eligible > cutoff:
            cutoff = eligible
            candidate, changed = build(cutoff)
            after = self.estimate(candidate) if changed else before
        if not changed or after >= before:
            detail["reason"] = (
                "below_threshold" if before <= self.threshold else "no_safe_reduction"
            )
            return body, detail
        # Commit a prefix only after every patch and estimate succeeds. Appending
        # turns keeps it unchanged until the reduced request crosses the threshold.
        reused = reusable and cutoff == self.cutoff
        self.cutoff, self.prefix = cutoff, fingerprint(cutoff)
        detail.update(
            applied=True,
            reason="reused_prefix" if reused else "compacted",
            estimated_after=after,
            target_met=after <= self.threshold,
            cutoff=cutoff,
            shortened_values=changed,
        )
        return candidate, detail
