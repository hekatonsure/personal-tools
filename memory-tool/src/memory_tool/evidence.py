"""Classify archive packaging, never truth or authorization. Raw records stay intact."""

import json
import re

CATALOG_VERSION = 3


STOP_WORDS = set(
    "a an and are as at be been by can did do does for from had has have how i in is it its me my of on or our so that the their them there they this to was we were what when where which who why will with would you your originally".split()
)


def query_words(query):
    words = list(dict.fromkeys(re.findall(r"\w+", query, flags=re.UNICODE)))
    useful = [w for w in words if w.casefold() not in STOP_WORDS]
    return (useful or words)[:24]


def source_kind(role, text):
    if role == "generated_memory" or (
        role == "developer"
        and (text.startswith("# Connectome memory") or "MEMORY-TOOL/v1" in text)
    ):
        return "generated"
    head = text[:2000]
    if role == "developer" and any(
        marker in head
        for marker in (
            "<app-context>",
            "<permissions instructions>",
            "<skills_instructions>",
            "<image_resize_notice>",
            "# Tools",
            "You are Codex,",
        )
    ):
        return "scaffolding"
    if role == "assistant":
        try:
            value = json.loads(text)
        except ValueError:
            value = None
        if isinstance(value, dict) and set(value) == {
            "risk_level",
            "user_authorization",
            "outcome",
            "rationale",
        }:
            return "review_metadata"
    if (
        head.startswith("The following is the Codex agent history")
        or len(re.findall(r"(?m)^\[\d+\] (?:user|assistant|tool)", text)) >= 3
    ):
        return "transcript_replay"
    # Search/zoom output is a copy of earlier evidence. It remains reachable by ID.
    # Remove escaping for classification only; never alter the source or zoom offsets.
    plain = re.sub(r"\\+", "", text)
    if role == "tool_result" and (
        (
            '"hits"' in plain
            and '"evidence"' in plain
            and ('"candidates"' in plain or '"usage"' in plain)
        )
        or ('"next_offset"' in plain and '"event"' in plain and '"text"' in plain)
        or all(key in plain for key in ('"event":', '"offset":', '"score":', '"text":'))
    ):
        return "retrieval_echo"
    return "tool_call" if role == "tool_call" else "source"


ECHO_KINDS = {
    "generated",
    "transcript_replay",
    "retrieval_echo",
    "scaffolding",
    "review_metadata",
}
