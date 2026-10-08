"""Classify archive packaging, never truth or authorization. Raw records stay intact."""

import json
import re

CATALOG_VERSION = 5
HARNESS_PREFIXES = (
    "# AGENTS.md instructions",
    "<environment_context>",
    "<skill>",
    "<turn_aborted>",
    "<multi_agent_mode>",
    "<multi_agent_role>",
    "<apps_instructions>",
    "You are `/root`, the primary agent",
    "[connectome memory checkpoint",
)


STOP_WORDS = set(
    "a an and are as at be been by can did do does for from had has have how i in is it its me my of on or our so that the their them there they this to was we were what when where which who why will with would you your originally".split()
)


def query_words(query):
    words = list(dict.fromkeys(re.findall(r"\w+", query, flags=re.UNICODE)))
    useful = [w for w in words if w.casefold() not in STOP_WORDS]
    return (useful or words)[:24]


def decision_question(query):
    return bool(
        re.search(
            r"\b(?:why|cho[os]se|chose|decid\w*|decis\w*|prefer\w*|said|agreed|authoriz\w*)\b",
            query,
            re.I,
        )
    )


def retrieval_request(text):
    """Recognize retrieval-only calls, not arbitrary code mentioning memory tools."""
    try:
        call = json.loads(text)
    except (ValueError, TypeError):
        return False
    if not isinstance(call, dict):
        return False
    name = call.get("name", "")
    if not isinstance(name, str):
        return False
    is_read = lambda n: (
        n.split("__")[-1] in {"memory_search", "memory_zoom", "memory_status"}
    )
    if is_read(name):
        return True
    if name not in {"exec", "functions.exec"}:
        return False
    code = call.get("input", "")
    if not isinstance(code, str):
        return False
    calls = re.findall(r"\btools\.(\w+)\s*\(", code)
    # Mixed batches with writes or other evidence-producing tools stay searchable.
    if not calls or not all(is_read(n) or n == "exec_command" for n in calls):
        return False
    commands = re.findall(r"\bcmd\s*:\s*(['\"])(.*?)\1", code, re.S)
    return len(commands) == calls.count("exec_command") and all(
        command.startswith("session-search ")
        and not re.search(r"[;&|`\n]|\$\(", command)
        for _, command in commands
    )


def source_kind(role, text):
    if role == "tool_call" and retrieval_request(text):
        return "retrieval_call"
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
    # Harness injections arrive as user/developer turns and repeat in every
    # resumed or forked session. They carry no conversation facts.
    if role in {"user", "developer"} and head.lstrip().startswith(HARNESS_PREFIXES):
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
        # Connectome snapshot workers emit this exact schema as assistant JSON.
        # Their inferred summaries are not fresh decisions by the original speaker.
        if (
            isinstance(value, dict)
            and set(value) == {"now", "facts", "hypotheses", "stance", "pins", "todo"}
            and isinstance(value["now"], str)
            and all(
                isinstance(value[k], list)
                for k in ("facts", "hypotheses", "pins", "todo")
            )
        ):
            # Keep the original assistant role during future capture/replays.
            # `generated` has a separate capture-time role conversion contract.
            return "snapshot_summary"
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
        or all(
            key in plain
            for key in ('"source_events":', '"agent_feedback":', '"native_recovery":')
        )
    ):
        return "retrieval_echo"
    return "tool_call" if role == "tool_call" else "source"


ECHO_KINDS = {
    "generated",
    "snapshot_summary",
    "transcript_replay",
    "retrieval_echo",
    "scaffolding",
    "review_metadata",
}
