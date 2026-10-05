"""Install reviewable native hooks, preserving existing definitions and trust state."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import re


def definitions(executable, connectome=None):
    command = f'"{executable}" hook'
    if connectome:
        command += f' --connectome "{connectome}"'
    base = {
        "type": "command",
        "command": command,
        "commandWindows": "& " + command,
        "timeout": 30,
    }
    return {
        "PreCompact": {
            "hooks": [
                {**base, "statusMessage": "memory-tool: checkpointing project history"}
            ]
        },
        # Capturing each finished turn keeps search current between compactions.
        "Stop": {"hooks": [base]},
        "SessionStart": {
            "matcher": "^(compact|resume|clear|startup)$",
            "hooks": [
                {
                    **base,
                    "additionalContextLimit": 64000,
                    "statusMessage": "memory-tool: restoring project history",
                }
            ],
        },
    }


def install(path, executable, connectome=None):
    path = Path(path)
    original = (
        json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"hooks": {}}
    )
    hooks = original.setdefault("hooks", {})
    added = []
    for event, group in definitions(executable, connectome).items():
        groups = hooks.setdefault(event, [])
        if group in groups:
            continue
        # Refuse a silent second memory-tool installation with changed arguments.
        if any(
            "memory-tool" in h.get("command", "")
            for g in groups
            for h in g.get("hooks", [])
        ):
            raise ValueError(
                f"Existing memory-tool {event} definition differs; review it before updating"
            )
        groups.append(group)
        added.append(event)
    if added:
        if path.exists():
            suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            shutil.copy2(
                path, path.with_name(path.name + ".before-memory-tool-" + suffix)
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".memory-tool.tmp")
        temporary.write_text(json.dumps(original, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    return {
        "path": str(path),
        "added": added,
        "trust": "Not modified. New definitions require Codex /hooks review.",
    }


def context_target(path, reset_at, context_limit):
    if not 64000 < reset_at < context_limit:
        raise ValueError("Require 64000 < reset-at < context-limit")
    path = Path(path)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    section = re.search(r"(?m)^\s*\[", text)
    index = section.start() if section else len(text)
    header, tables = text[:index], text[index:]
    for key, value in (
        ("model_context_window", context_limit),
        ("model_auto_compact_token_limit", reset_at),
    ):
        pattern = rf"(?m)^{key}\s*=.*$"
        if re.search(pattern, header):
            header = re.sub(pattern, f"{key} = {value}", header)
        else:
            header = f"{key} = {value}\n" + header
    updated = header + tables
    if updated != text:
        if path.exists():
            shutil.copy2(
                path,
                path.with_name(
                    path.name
                    + ".before-memory-target-"
                    + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                ),
            )
        path.write_text(updated, encoding="utf-8")
    return {"reset_at": reset_at, "context_target": context_limit}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--path", default=str(Path.home() / ".codex/hooks.json"))
    p.add_argument(
        "--executable",
        default=shutil.which("memory-tool"),
        required=not bool(shutil.which("memory-tool")),
    )
    p.add_argument("--connectome")
    p.add_argument(
        "--reset-at",
        type=int,
        help="Optionally set native auto-compaction target in adjacent config.toml",
    )
    p.add_argument("--context-limit", type=int, default=200000)
    a = p.parse_args()
    if not Path(a.executable).is_file():
        p.error("memory-tool executable does not exist")
    result = install(a.path, a.executable, a.connectome)
    if a.reset_at:
        result["native_context"] = context_target(
            Path(a.path).with_name("config.toml"), a.reset_at, a.context_limit
        )
    print(json.dumps(result, indent=2))
