"""Install Claude hooks and user-scope MCP without starting Claude or changing auth.

Hooks live in ~/.claude/settings.json; user MCP entries live in ~/.claude.json.
Use --dry-run to validate and print only the definitions this installer would add.
Existing hooks (including Supermemory), permissions and server entries are retained.
"""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shlex
import shutil
import tempfile


def definitions(executable, chat, db=None):
    args = [str(executable)]
    if db:
        args += ["--db", str(db)]
    # Claude command hooks run through a shell, including Git Bash on Windows.
    base = {
        "type": "command",
        "command": shlex.join([*args, "hook", "--backend", "claude"]),
        "timeout": 30,
    }
    hooks = {
        "PreCompact": {"hooks": [dict(base)]},
        "Stop": {"hooks": [dict(base)]},
        "SessionStart": {
            "matcher": "^(compact|resume|clear|startup)$",
            "hooks": [dict(base)],
        },
    }
    server = {
        "type": "stdio",
        "command": args[0],
        "args": [*args[1:], "serve", "--chat", chat],
    }
    return hooks, server


def read_config(path):
    raw = path.read_bytes() if path.exists() else None
    value = json.loads(raw) if raw is not None else {}
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return raw, value


def write_config(path, original, value):
    # Refuse to overwrite a config changed while we were planning this update.
    current = path.read_bytes() if path.exists() else None
    if current != original:
        raise ValueError(f"Configuration changed during installation: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    if original is not None:
        suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        shutil.copy2(path, path.with_name(path.name + ".before-memory-tool-" + suffix))
    descriptor, name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
        if original is not None:
            shutil.copymode(path, temporary)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def install(settings_path, mcp_path, executable, chat, db=None, dry_run=False):
    settings_path, mcp_path = Path(settings_path), Path(mcp_path)
    if settings_path.resolve() == mcp_path.resolve():
        raise ValueError(
            "Claude hook settings and MCP configuration must be separate files"
        )
    if not Path(executable).is_absolute() or not Path(executable).is_file():
        raise ValueError("Use an existing absolute memory-tool executable")
    if db and not Path(db).is_absolute():
        raise ValueError("Use an absolute database path")
    settings_raw, settings = read_config(settings_path)
    mcp_raw, mcp = read_config(mcp_path)
    hooks = settings.setdefault("hooks", {})
    servers = mcp.setdefault("mcpServers", {})
    if not isinstance(hooks, dict) or not isinstance(servers, dict):
        raise ValueError("Expected hooks and mcpServers objects")
    new_hooks, server = definitions(executable, chat, db)
    added = []
    # Validate both files before writing either, including conflicting installs.
    for event, group in new_hooks.items():
        groups = hooks.setdefault(event, [])
        if group in groups:
            continue
        if any(
            "memory-tool" in h.get("command", "")
            for g in groups
            for h in g.get("hooks", [])
        ):
            raise ValueError(
                f"Existing memory-tool {event} differs; review it before updating"
            )
        groups.append(group)
        added.append(event)
    if "memory_tool" in servers and servers["memory_tool"] != server:
        raise ValueError(
            "Existing memory_tool MCP server differs; review it before updating"
        )
    mcp_added = "memory_tool" not in servers
    servers["memory_tool"] = server
    result = {
        "settings_path": str(settings_path),
        "mcp_path": str(mcp_path),
        "added_hooks": added,
        "added_mcp": mcp_added,
        "dry_run": dry_run,
    }
    if dry_run:
        result.update({"hook_definitions": new_hooks, "mcp_definition": server})
    else:
        if added:
            write_config(settings_path, settings_raw, settings)
        if mcp_added:
            write_config(mcp_path, mcp_raw, mcp)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", default=str(Path.home() / ".claude/settings.json"))
    parser.add_argument("--mcp-path", default=str(Path.home() / ".claude.json"))
    parser.add_argument(
        "--executable",
        default=shutil.which("memory-tool"),
        required=not bool(shutil.which("memory-tool")),
    )
    parser.add_argument(
        "--chat",
        required=True,
        help="Existing default logical chat; tools also accept project",
    )
    parser.add_argument(
        "--db", help="Optional absolute archive path, shared by hooks and MCP"
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = install(
        args.path, args.mcp_path, args.executable, args.chat, args.db, args.dry_run
    )
    print(json.dumps(result, indent=2))
