"""Retire legacy hooks/MCP, retaining archives and memory-tool hook trust.

Dry-run unless --apply. Existing definitions that remain are never rewritten.
"""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import tomllib


def hooks_without_connectome(original):
    updated = json.loads(json.dumps(original))
    removed = []
    for event, groups in updated.get("hooks", {}).items():
        kept = []
        for group in groups:
            remaining = []
            for hook in group.get("hooks", []):
                commands = [
                    hook.get(key, "").replace("\\", "/")
                    for key in ("command", "commandWindows")
                ]
                legacy = any(
                    re.search(r"/codex-connectome/src/(?:hook|capture)\.ts[\"\s]*$", c)
                    for c in commands
                )
                if legacy:
                    removed.append({"event": event, "hook": hook})
                else:
                    remaining.append(hook)
            if remaining or "hooks" not in group:
                kept.append(
                    {**group, "hooks": remaining} if "hooks" in group else group
                )
        updated["hooks"][event] = kept
    return updated, removed


def disable_mcp(text):
    original = tomllib.loads(text)
    server = original.get("mcp_servers", {}).get("connectome_notes")
    if server is None or server.get("enabled") is False:
        return text
    pattern = r"(?ms)^\[mcp_servers\.connectome_notes\][^\n]*\n(?P<body>.*?)(?=^\[|\Z)"
    match = re.search(pattern, text)
    if not match:
        raise ValueError(
            "Unrecognized Connectome MCP table; configuration left unchanged"
        )
    body = match.group("body")
    replacement = re.sub(r"(?m)^enabled\s*=.*$", "enabled = false", body)
    if replacement == body:
        replacement = "enabled = false\n" + body
    result = text[: match.start("body")] + replacement + text[match.end("body") :]
    expected = json.loads(json.dumps(original))
    expected["mcp_servers"]["connectome_notes"]["enabled"] = False
    if tomllib.loads(result) != expected:
        raise ValueError("Unexpected settings change")
    return result


def retire(hooks_path, config_path, apply=False):
    hooks_path, config_path = Path(hooks_path), Path(config_path)
    original = json.loads(hooks_path.read_text(encoding="utf-8"))
    updated, removed = hooks_without_connectome(original)
    config = config_path.read_text(encoding="utf-8")
    new_config = disable_mcp(config)
    changes = []
    if updated != original:
        changes.append((hooks_path, json.dumps(updated, indent=2) + "\n"))
    if config != new_config:
        changes.append((config_path, new_config))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backups = []
    if apply:
        for path, _ in changes:
            backup = path.with_name(
                path.name + ".before-connectome-retirement-" + stamp
            )
            shutil.copy2(path, backup)
            backups.append(str(backup))
        for path, text in changes:
            temporary = path.with_name(path.name + ".retire-connectome.tmp")
            temporary.write_text(text, encoding="utf-8")
            temporary.replace(path)
    return {
        "applied": apply,
        "removed_hooks": len(removed),
        "changed_files": [str(p) for p, _ in changes],
        "backups": backups,
        "archives": "preserved",
        "trust_records": "not modified",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hooks", type=Path, default=Path.home() / ".codex/hooks.json")
    parser.add_argument(
        "--config", type=Path, default=Path.home() / ".codex/config.toml"
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(retire(args.hooks, args.config, args.apply), indent=2))
