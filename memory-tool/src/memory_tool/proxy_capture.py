"""Public evidence extraction for the proxy; never persists raw HTTP bodies.

Transport forwards bytes independently. These parsers deliberately omit thinking,
encrypted reasoning, auth headers, and unknown block types from durable storage.
"""

import hashlib
import json
import sys
import uuid

from .archive import Archive, digest


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def public_content(content):
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    result = []
    for block in content or []:
        kind = block.get("type")
        if kind in {"text", "input_text", "output_text"}:
            result.append({"type": "text", "text": block["text"]})
        elif kind == "refusal":
            result.append({"type": "text", "text": block["refusal"]})
        elif kind in {"tool_use", "server_tool_use"}:
            result.append(
                {k: block[k] for k in ("type", "id", "name", "input") if k in block}
            )
        elif kind == "tool_result":
            result.append(
                {
                    "type": kind,
                    "tool_use_id": block.get("tool_use_id"),
                    "is_error": block.get("is_error", False),
                    "content": public_content(block.get("content", [])),
                }
            )
        elif kind in {"image", "input_image", "input_file", "document"}:
            # Preserve a findable fingerprint without copying huge opaque payloads.
            result.append({"type": kind, "sha256": digest(encoded(block))})
    return result


def public_items(items):
    if isinstance(items, str):
        items = [{"role": "user", "content": items}]
    result = []
    for item in items or []:
        kind = item.get("type", "message")
        if kind == "message":
            role = item.get("role", "assistant")
            if role not in {"user", "assistant", "system", "developer"}:
                continue
            for block in public_content(item.get("content", [])):
                block_kind = block["type"]
                block_role = (
                    "tool_call"
                    if block_kind in {"tool_use", "server_tool_use"}
                    else "tool_result"
                    if block_kind == "tool_result"
                    else role
                )
                result.append((block_role, block))
        elif kind in {"function_call", "custom_tool_call"}:
            result.append(
                (
                    "tool_call",
                    {
                        k: item[k]
                        for k in (
                            "type",
                            "id",
                            "call_id",
                            "name",
                            "arguments",
                            "input",
                        )
                        if k in item
                    },
                )
            )
        elif kind in {"function_call_output", "custom_tool_call_output"}:
            output = item.get("output", "")
            if isinstance(output, list):
                output = public_content(output)
            result.append(
                (
                    "tool_result",
                    {
                        "type": kind,
                        "call_id": item.get("call_id"),
                        "output": output,
                    },
                )
            )
    return result


def request_items(provider, body):
    if provider == "anthropic":
        system = (
            [{"role": "system", "content": body["system"]}]
            if body.get("system")
            else []
        )
        return public_items([*system, *body.get("messages", [])])
    instructions = (
        [{"role": "system", "content": body["instructions"]}]
        if body.get("instructions")
        else []
    )
    return public_items(instructions) + public_items(body.get("input", []))


def response_items(provider, body, streaming):
    if not streaming:
        value = json.loads(body)
        if provider == "anthropic":
            return public_items([value]), value.get("type") == "message" and value.get(
                "role"
            ) == "assistant"
        return public_items(value.get("output", [])), value.get("status") == "completed"
    # The bounded copy is parsed after forwarding; stream framing/chunks on the
    # live connection are never changed by this parser.
    events = []
    data = []
    for line in body.decode("utf-8").splitlines():
        if line.startswith("data:"):
            data.append(line[5:].lstrip(" "))
        elif not line and data:
            payload = "\n".join(data)
            data = []
            if payload != "[DONE]":
                events.append(json.loads(payload))
    if provider == "openai":
        # Only a terminal event proves the complete output; output_item.done can
        # also occur before a failed or interrupted response.
        for event in reversed(events):
            if event.get("type") in {
                "response.completed",
                "response.incomplete",
                "response.failed",
            }:
                return public_items(event.get("response", {}).get("output", [])), event[
                    "type"
                ] == "response.completed"
        return [], False
    blocks, stopped = {}, set()
    complete = errored = False
    for event in events:
        kind, index = event.get("type"), event.get("index")
        if kind == "content_block_start":
            block = event["content_block"]
            # Keep reasoning out even of the reconstructed public object.
            if block.get("type") in {"text", "tool_use", "server_tool_use"}:
                blocks[index] = dict(block)
        elif kind == "content_block_delta" and index in blocks:
            delta, block = event["delta"], blocks[index]
            if delta.get("type") == "text_delta":
                block["text"] = block.get("text", "") + delta["text"]
            elif delta.get("type") == "input_json_delta":
                block["_json"] = block.get("_json", "") + delta["partial_json"]
        elif kind == "content_block_stop" and index in blocks:
            block = blocks[index]
            if "_json" in block:
                block["input"] = json.loads(block.pop("_json"))
            stopped.add(index)
        elif kind == "message_stop":
            complete = True
        elif kind == "error":
            errored = True
    items = [{"role": "assistant", "content": [blocks[i] for i in sorted(stopped)]}]
    return public_items(items), complete and not errored and set(blocks) == stopped


class Capture:
    """One HTTP exchange. Capture failures cannot change the forwarded response."""

    def __init__(
        self, db, chat, session, provider, request_body, content_encoding="identity"
    ):
        self.db, self.chat, self.session, self.provider = db, chat, session, provider
        self.id = uuid.uuid4().hex
        self.detail = {
            "provider": provider,
            "request_bytes": len(request_body),
            "request_sha256": hashlib.sha256(request_body).hexdigest(),
            "input_capture": "pending",
            "output_capture": "pending",
        }
        self.input_failed = False
        self.result_sources = {}
        self.input_events = []
        self.request_stream = False
        self._safe(
            lambda archive: self._request(archive, request_body, content_encoding)
        )

    def _safe(self, action):
        archive = None
        try:
            archive = Archive(self.db)
            action(archive)
        except Exception as error:
            self.input_failed = True
            # No exception messages: they may include credentials or body text.
            print(
                f"memory-tool proxy: capture failed ({type(error).__name__}); forwarding continues",
                file=sys.stderr,
            )
        finally:
            if archive is not None:
                archive.close()

    def _save(self, archive, items, direction, replay_key=None):
        prefix = self.provider
        result_sources = {}
        for index, (role, block) in enumerate(items):
            text = block["text"] if block["type"] == "text" else encoded(block)
            key = digest(role + ":" + encoded(block))
            # Include the preceding public history, so the same words in a changed
            # conversation remain distinct. Only replayed prefixes share an ID.
            prefix = digest(prefix + ":" + key)
            source = f"proxy:{self.session}:{direction}:{replay_key or self.id}:{index}:{prefix}"
            event = archive.append(
                self.chat,
                role,
                text,
                source,
                encoded(
                    {
                        "provider": self.provider,
                        "direction": direction,
                        "item": block,
                    }
                ),
            )
            if direction == "input":
                self.input_events.append(event)
            if role == "tool_result":
                identity = block.get("tool_use_id", block.get("call_id"))
                if identity is not None:
                    result_sources[identity] = (
                        event if identity not in result_sources else None
                    )
        return result_sources

    def _request(self, archive, body, encoding):
        if encoding not in {"", "identity"}:
            self.detail["input_capture"] = "unsupported_encoding"
        else:
            value = json.loads(body)
            self.request_stream = value.get("stream") is True
            # Repeated full-history requests share source identities; incremental
            # Responses requests must remain distinct even if the text repeats.
            key = (
                None
                if value.get("previous_response_id") or value.get("conversation")
                else "history"
            )
            self.result_sources = self._save(
                archive, request_items(self.provider, value), "input", key
            )
            self.detail["input_capture"] = "recorded"
        archive.operation(
            "proxy:" + self.id, self.chat, self.session, "forwarding", self.detail
        )

    def finish(
        self,
        body,
        *,
        status,
        content_type,
        encoding,
        size,
        sha256,
        delivered,
        overflow=False,
    ):
        def save(archive):
            # Codex's ChatGPT Responses route can omit Content-Type on SSE.
            # Only infer streaming for an explicitly streaming request with no
            # response media type; the parser still requires a terminal event.
            streaming = "text/event-stream" in content_type or (
                not content_type and self.request_stream
            )
            self.detail.update(
                {
                    "status": status,
                    "response_bytes": size,
                    "response_sha256": sha256,
                    "response_content_type": content_type.split(";", 1)[0][:80],
                    "delivered": delivered,
                }
            )
            if self.input_failed:
                self.detail["input_capture"] = "failed"
            if overflow:
                self.detail["output_capture"] = "size_limit"
            elif not delivered or status is None or not 200 <= status < 300:
                self.detail["output_capture"] = "not_completed"
            elif encoding not in {"", "identity"}:
                self.detail["output_capture"] = "unsupported_encoding"
            elif "json" not in content_type and not streaming:
                self.detail["output_capture"] = "unsupported_content_type"
            else:
                try:
                    items, complete = response_items(self.provider, body, streaming)
                    # Incomplete content is not presented as a finished assistant turn.
                    if complete:
                        self._save(archive, items, "output")
                    self.detail["output_capture"] = (
                        "recorded" if complete else "incomplete"
                    )
                except (ValueError, TypeError, KeyError, AttributeError):
                    self.detail["output_capture"] = "parse_failed"
            archive.operation(
                "proxy:" + self.id,
                self.chat,
                self.session,
                "complete" if delivered else "interrupted",
                self.detail,
            )

        self._safe(save)
