"""Local presentation policy. Never edits evidence or turns history into permission."""

import hashlib
import json
import re
import time
from collections import Counter
from datetime import datetime, timezone

from .evidence import query_words


POLICY_VERSION = 5
STATUS_TTL = 6 * 3600
LABEL_VERSION = 1
# Jev label probabilities that make an older event worth carrying. Below the
# floor on all of them it is routine traffic; zoom and search still reach it.
CONTENT_LABELS = ("decision", "user_constraint", "outcome", "failure", "plan")
LOW_VALUE = 0.25


def verdict(row):
    return (row.get("feedback") or "").partition(":")[0] or None


def importance(row):
    # An agent that found the record useful outranks the labeler's guess.
    if verdict(row) == "useful":
        return 1.0
    labels = row.get("labels")
    return max(labels[k] for k in CONTENT_LABELS) if labels else None


GENERIC = set(
    "work working please thanks thank great now next continue keep going everything anything thing things want wanted use used using make made tool tools result results current project session chat context memory agent assistant user codex".split()
)
GENERIC.update(
    "all same time might just feel feels wait like probably necessary also default normally talk thoughts really well right sure yes okay version review final can could would should help understand about anything everything someone something years year now actually".split()
)
GENERIC.difference_update({"memory", "context", "codex"})
GENERIC.update(
    "then than not but give kind model models system whatever two one first last each every any only much more most very some such still back even into out over under up down off again after before because both during until while through should how cannot dont doesnt didnt cant isnt whats lets btw btw thing lot really need needs know think mean meant sure stuff got done getting tell wanted get do does did have had has may might must seems seem perhaps basically pretty already also its im youre thats theyre theirs their ours them us those these here there today tomorrow yesterday anymore anything everything nothing something without within instead enough necessary necessarily please see show look start end finish finished happy better best want wants keep going work works working use uses using change changes".split()
)


def timestamp(value):
    if value is None:
        return None
    try:
        if str(value).replace(".", "", 1).isdigit():
            number = float(value)
            return number / 1e9 if number > 1e15 else number
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return (
            parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        ).timestamp()
    except (ValueError, OverflowError):
        return None


def session_of(row):
    if row.get("session_id"):
        return row["session_id"]
    parts = row.get("source", "").split(":")
    return (
        parts[1]
        if len(parts) > 2
        and parts[0] in {"native", "codex", "claude", "proxy", "connectome", "dynamic"}
        else None
    )


def list_piece(value, depth):
    if not isinstance(value, dict):
        return None
    if isinstance(value.get("text"), str):
        return unpack_text(value["text"], depth + 1)
    # Base64 image bytes are unreadable in a prompt. The digest keeps different
    # images distinct for grouping; zoom still returns the stored data.
    if isinstance(value.get("image_url"), str):
        return f"[image sha256:{hashlib.sha256(value['image_url'].encode()).hexdigest()[:16]}]"
    return None


def image_free(text):
    return re.sub(r"data:image/[\w.+-]+;base64,[A-Za-z0-9+/=]*", "[image data]", text)


def unpack_text(text, depth=0):
    """Remove known transport envelopes only; raw offsets always refer to stored text."""
    if depth > 5:
        return text
    try:
        value = json.loads(text)
    except (ValueError, TypeError):
        return text
    if isinstance(value, str):
        return unpack_text(value, depth + 1)
    if isinstance(value, list):
        pieces = [list_piece(v, depth) for v in value]
        return "\n".join(pieces) if pieces and None not in pieces else text
    if isinstance(value, dict):
        # Timing/transport IDs can differ for a repeated read; exit status, errors
        # and arbitrary payload fields are evidence and must remain distinguishable.
        telemetry = {"wall_time_seconds", "chunk_id", "original_token_count"}
        for key in ("output", "text"):
            if isinstance(value.get(key), str):
                plain = unpack_text(value[key], depth + 1)
                remaining = {
                    k: v for k, v in value.items() if k not in telemetry and k != key
                }
                if not remaining:
                    return plain
                return json.dumps(
                    {**remaining, key: plain}, ensure_ascii=False, sort_keys=True
                )
        if set(value) == {"value"} and isinstance(value["value"], dict):
            return unpack_text(json.dumps(value["value"]), depth + 1)
        if set(value) == {"content"} and isinstance(value["content"], list):
            return unpack_text(json.dumps(value["content"]), depth + 1)
    return text


def normalized(text):
    value = unpack_text(text)
    # Indentation can change executable meaning; backslashes may be literal data.
    return value.replace("\r\n", "\n")


def duplicate_key(row):
    text = normalized(row["text"])
    if row["role"] in {"tool_result", "tool_call", "developer"}:
        key = (row["role"], text)
    else:
        # Repeated human decisions on different dates remain independent evidence.
        key = (row["role"], row.get("ts"), text)
    return hashlib.sha256(json.dumps(key, ensure_ascii=False).encode()).hexdigest()


def focus_terms(events, session=None, explicit=None):
    if explicit:
        return list(dict.fromkeys(w.lower() for w in query_words(explicit)))[:16]
    users = [
        e
        for e in events
        if e["role"] == "user" and (not session or session_of(e) == session)
    ]
    if not users:
        return []
    counts = Counter()
    substantive = 0
    for row in reversed(users[-32:]):
        text = row.get("text", row.get("preview", ""))
        if (
            text.lstrip().startswith("<")
            and "send_user_message_question_reply" not in text
        ):
            continue
        words = {word.lower() for word in re.findall(r"[\w-]+", text)}
        words = {
            word
            for word in words
            if len(word) >= 3
            and word not in GENERIC
            and word
            not in {
                "the",
                "and",
                "for",
                "from",
                "with",
                "that",
                "this",
                "was",
                "were",
                "you",
                "your",
                "our",
                "are",
                "can",
                "will",
                "would",
                "about",
                "what",
                "when",
                "where",
                "why",
                "who",
                "which",
            }
            and not word.isdigit()
        }
        if not words:
            continue
        for word in sorted(words):
            counts[word] += 1 / (substantive + 1)
        substantive += 1
        if substantive == 8:
            break
    return [w for w, _ in counts.most_common(12)]


def relevance(text, terms):
    words = set(re.findall(r"[\w-]+", text.lower()))
    return sum(
        1 for term in terms if term in words or ("-" in term and term in text.lower())
    )


def freshness(record, now=None):
    now = time.time() if now is None else now
    text = record.get("text", record.get("preview", ""))
    declared = record.get("memory_kind")
    transient = declared == "status" or (
        declared != "preference"
        and bool(
            re.search(
                r"\b(?:currently running|still running|active (?:process|rental|pod|worker)|running(?:SSH|output| process| experiment)|in.progress|needs? (?:one |a |an |another )?(?:restart|reload)|restart (?:required|needed)|pending approval|PID\s*[:=]?\s*\d+|deadline)\b",
                text,
                re.I,
            )
        )
    )
    expiry = timestamp(record.get("expires_at"))
    observed = timestamp(record.get("verified_at", record.get("ts")))
    if expiry is not None:
        return "expired" if now >= expiry else "time_limited"
    if transient:
        if observed is None:
            return "unverified_status"
        return "expired" if now - observed > STATUS_TTL else "recent_status_unverified"
    return "historical_evidence"


def claims(record):
    """Conservative state extraction; unknown prose stays evidence, not resolved fact."""
    supplied = record.get("claims", [])
    if supplied:
        return [
            c
            for c in supplied
            if isinstance(c, dict)
            and all(isinstance(c.get(k), str) for k in ("key", "value"))
        ]
    text = record.get("text", "")
    scope = session_of(record)
    if not scope:
        return []
    if "?" in text or re.search(
        r"\b(?:if|whether|maybe|hypothesis|unverified|pending)\b|not verified",
        text,
        re.IGNORECASE,
    ):
        return []
    # A completed verification overrides a requirement only within the same session.
    if re.search(
        r"(?:no (?:further|another|more|repeat) restart|(?:restart|reconnect) (?:worked|verified|complete)|after (?:latest |user )?restart.*(?:now exposes|all.?\d.?tools))",
        text,
        re.I,
    ):
        return [{"key": f"restart:{scope}", "value": "verified", "corrects": True}]
    if re.search(
        r"(?:needs?|requires?) (?:one |a |an |another )?(?:app |MCP )?(?:restart|reload)|restart (?:required|needed)",
        text,
        re.I,
    ):
        return [{"key": f"restart:{scope}", "value": "required"}]
    return []


def select_notes(notes, terms, session=None, now=None):
    eligible, omitted = [], Counter()
    for note in notes:
        state = freshness(note, now)
        if state in {"expired", "unverified_status"}:
            omitted[state] += 1
            continue
        score = relevance(note.get("text", ""), terms)
        same = bool(session and session_of(note) == session)
        durable = note.get("memory_kind") == "preference"
        if session and session_of(note) and not same and not durable:
            omitted["other_session"] += 1
            continue
        if terms and not score and not durable:
            omitted["off_topic"] += 1
            continue
        if session and session_of(note) and not same and not score and not durable:
            omitted["other_session"] += 1
            continue
        eligible.append(
            {
                **note,
                "freshness": state,
                "_rank": (
                    durable,
                    score,
                    same,
                    note.get("type") == "pin",
                    timestamp(note.get("ts")) or 0,
                ),
            }
        )
    groups = {}
    for note in eligible:
        for claim in claims(note):
            groups.setdefault(claim["key"], []).append((note, claim))
    resolutions, superseded = [], set()
    for key, members in groups.items():
        members.sort(key=lambda pair: timestamp(pair[0].get("ts")) or 0)
        values = {c["value"] for _, c in members}
        latest, claim = members[-1]
        latest_time = timestamp(latest.get("ts"))
        unique_latest = latest_time is not None and all(
            (timestamp(n.get("ts")) or 0) < latest_time for n, _ in members[:-1]
        )
        resolved = len(values) == 1 or (claim.get("corrects") is True and unique_latest)
        resolutions.append(
            {
                "key": key,
                "state": "resolved" if resolved else "unresolved",
                "value": claim["value"] if resolved else None,
                "sources": [
                    {"note": n["id"], "ts": n.get("ts"), "value": c["value"]}
                    for n, c in members
                ],
            }
        )
        if resolved and len(values) > 1:
            # Avoid removing multi-fact prose: suppress only dedicated state notes.
            for n, _ in members[:-1]:
                if len(claims(n)) == 1 and (
                    n.get("memory_kind") == "status" or len(n.get("text", "")) < 220
                ):
                    superseded.add(n["id"])
    selected, seen = [], set()
    for note in sorted(eligible, key=lambda n: n["_rank"], reverse=True):
        if note["id"] in superseded:
            omitted["corrected"] += 1
            continue
        key = normalized(note["text"])
        if key in seen:
            omitted["duplicate"] += 1
            continue
        seen.add(key)
        selected.append({k: v for k, v in note.items() if k != "_rank"})
    return selected, resolutions, dict(omitted)


def event_view(row, now=None):
    role, text = row["role"], row["text"]
    result = {
        "event": row["id"],
        "role": role,
        "ts": row["ts"],
        "freshness": freshness(row, now),
        **({"agent_feedback": row["feedback"]} if row.get("feedback") else {}),
    }
    if role in {"tool_call", "tool_result"} and len(text) > 1600:
        plain = unpack_text(text)
        important = [
            line[:360]
            for line in plain.splitlines()
            if re.search(
                r"error|exception|failed|exit.code|passed|saved|commit", line, re.I
            )
        ]
        result.update(
            {
                "presentation": "literal excerpts; complete raw text via memory_zoom",
                "text": plain[:280]
                + "\n[...]\n"
                + "\n".join(important[:3])
                + "\n[...]\n"
                + plain[-280:],
            }
        )
    else:
        result["text"] = text
    return result
