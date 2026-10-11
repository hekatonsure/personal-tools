"""Readable, local navigation hints. Labels are not summaries or verified facts."""

from collections import Counter
from pathlib import PureWindowsPath
import re

from .selection import GENERIC, freshness, relevance, verdict


STOP = GENERIC | set(
    "oct sep jan feb mar apr jun jul aug nov dec utc users hefey documents local path source text event note notes history historical evidence fact pin type true false null passed tests test verified complete completed installed installation update updated commit git main json python memory-tool native windows tools program instruction status use user explicitly requested reported current old new running active".split()
)
STOP.update(
    "the and for with from that this was were are will would been being into onto than then when where which while whose whom through under above below between against does did doing having have has had our your their its you they them his her she him not nor but also via per can could should must may might any all both either neither each other another such only own same because why how what there here these those http https com org true false input output call name function exec command params arguments content payload timestamp script token tokens summary status completed stdout stderr result results tool-call tool-result".split()
)


def words(text):
    return [
        w
        for w in re.findall(r"\b[a-z][a-z-]{2,30}\b", text.lower())
        if w not in STOP
        and not w.startswith(("http", "sha"))
        and not re.fullmatch(r"[a-f]+", w)
    ]


def keywords(texts, count=3):
    counts = Counter(w for text in texts for w in set(words(text)))
    return (
        ", ".join(
            w for w, _ in sorted(counts.items(), key=lambda p: (-p[1], p[0]))[:count]
        )
        or "general records"
    )


def note_groups(notes, now=None):
    """Explicit kind/document first; otherwise shared keywords, never inferred truth."""
    families = {
        "Memory and agent setup": r"memory|connectome|optmem|retrieval|compaction|hooks|codex",
        "Cloud operations": r"cloud|runpod|vast|rental|paidpod|gpu",
        "Experiments and evaluation": r"experiment|benchmark|seed|evaluation|sweep|campaign",
        "Software implementation": r"implementation|compiler|bytecode|build|testing|runtime",
    }
    groups = {}
    for n in notes:
        kind = n.get("memory_kind")
        if verdict(n) in {"stale", "noise", "wrong"} or freshness(n, now) in {
            "expired",
            "unverified_status",
        }:
            label = "Operational or flagged history (verify before use)"
        elif kind in {"decision", "preference"}:
            label = "Decisions and preferences"
        elif kind == "commitment":
            label = "Recorded commitments (check completion)"
        elif n.get("document"):
            label = "Document: " + PureWindowsPath(n["document"]).stem.replace("-", " ")
        else:
            # Coarse local facets, not model-generated summaries. Give the
            # opening topic priority over incidental mentions in long notes.
            scores = {
                name: len(re.findall(pattern, n["text"][:600], re.I))
                + 3 * len(re.findall(pattern, n["text"][:100], re.I))
                for name, pattern in families.items()
            }
            label = (
                max(scores, key=scores.get)
                if max(scores.values())
                else "Other findings"
            )
        groups.setdefault(label, []).append(n)
    ordered = sorted(
        groups.items(), key=lambda p: ("history" in p[0], -len(p[1]), p[0])
    )
    if len(ordered) > 8:
        ordered = ordered[:7] + [
            ("Other topics", [n for _, group in ordered[7:] for n in group])
        ]
    return ordered


def render_index(
    archive, chat, notes, note_root, events, session, counter, budget, terms=()
):
    """Every shown reference resolves; whole lines only, including at small budgets."""
    from .knowledge import tree_zoom
    from .summary_tree import SummaryTree
    import json

    lines = [
        "\nMEMORY INDEX — zoom for detail; labels are navigation hints, not current facts.\n"
    ]
    if note_root:
        lines.append(f"Notes: {len(notes)} records — note_tree: {note_root}\n")
        root = json.loads(tree_zoom(archive, chat, note_root[5:]))
        children = sorted(
            root.get("children", []),
            key=lambda child: (
                "history" in child.get("label", ""),
                -relevance(child.get("label", ""), terms),
                -relevance(child.get("text", ""), terms),
            ),
        )
        for child in children:
            lines.append(
                f"  {child.get('label', 'Notes')}: {child['count']} notes — {child['event']}\n"
            )
    if events:
        tree = SummaryTree(
            archive,
            chat,
            f"native-index:{session or 'project'}",
            max_lines=8,
            node_bytes=256,
            max_calls=0,
        )
        roots = tree.advance([e["id"] for e in events])
        lines.append(
            "Conversation archive (chronological; includes omitted operational history):\n"
        )
        by_id = {e["id"]: e for e in events}
        for node in roots:
            first, last = by_id[node["first"]], by_id[node["last"]]
            passages = [
                e["preview"][:400]
                for e in events
                if node["first"] <= e["id"] <= node["last"]
                and e["role"] in {"user", "assistant"}
            ]
            label = keywords(passages) if passages else "tool activity"
            lines.append(
                f"  {str(first['ts'])[:10]}..{str(last['ts'])[:10]}: {node['count']} events; {label} — tree:{node['id']}\n"
            )
    lines.append(
        'Open a branch with memory_zoom(event="tree:…", project="'
        + archive.project(chat)
        + '"). Follow children to exact note/event references; use memory_search for a question.\n'
    )
    result = ""
    for line in lines:
        if counter.count(result + line) <= budget:
            result += line
    return result
