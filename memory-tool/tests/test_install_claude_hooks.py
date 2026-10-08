import importlib.util
import json
from pathlib import Path
import shlex

import pytest


spec = importlib.util.spec_from_file_location(
    "install_claude_hooks",
    Path(__file__).parents[1] / "scripts/install_claude_hooks.py",
)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def configs(tmp_path):
    settings = tmp_path / "settings.json"
    mcp = tmp_path / ".claude.json"
    executable = tmp_path / "space and $dollar" / "memory-tool"
    executable.parent.mkdir()
    executable.touch()
    previous = {
        "permissions": {"allow": ["Read"]},
        "hooks": {
            "SessionStart": [
                {"hooks": [{"type": "command", "command": "supermemory-recall"}]}
            ]
        },
    }
    auth = {
        "opaqueSetting": "keep",
        "mcpServers": {"existing": {"type": "stdio", "command": "other"}},
    }
    settings.write_text(json.dumps(previous))
    mcp.write_text(json.dumps(auth))
    settings.chmod(0o600)
    mcp.chmod(0o600)
    return settings, mcp, executable, previous, auth


def test_install_preserves_settings_and_servers_and_is_idempotent(tmp_path):
    settings, mcp, executable, previous, auth = configs(tmp_path)
    original = {p: p.read_bytes() for p in (settings, mcp)}
    db = tmp_path / "private archive.sqlite"
    result = installer.install(settings, mcp, executable, "home", db)
    assert result["added_hooks"] == ["PreCompact", "Stop", "SessionStart"]
    assert result["added_mcp"]
    data = json.loads(settings.read_text())
    assert data["permissions"] == previous["permissions"]
    assert data["hooks"]["SessionStart"][0] == previous["hooks"]["SessionStart"][0]
    command = data["hooks"]["Stop"][0]["hooks"][0]
    assert shlex.split(command["command"]) == [
        str(executable),
        "--db",
        str(db),
        "hook",
        "--backend",
        "claude",
    ]
    assert "commandWindows" not in command and "additionalContextLimit" not in command
    servers = json.loads(mcp.read_text())
    assert servers["opaqueSetting"] == auth["opaqueSetting"]
    assert servers["mcpServers"]["existing"] == auth["mcpServers"]["existing"]
    assert servers["mcpServers"]["memory_tool"]["args"] == [
        "--db",
        str(db),
        "serve",
        "--chat",
        "home",
    ]
    installed = {p: p.read_bytes() for p in (settings, mcp)}
    again = installer.install(settings, mcp, executable, "home", db)
    assert again["added_hooks"] == [] and not again["added_mcp"]
    for path in (settings, mcp):
        assert path.read_bytes() == installed[path]
        backups = list(tmp_path.glob(path.name + ".before-memory-tool-*"))
        assert len(backups) == 1 and backups[0].read_bytes() == original[path]
        assert path.stat().st_mode & 0o777 == 0o600


def test_dry_run_does_not_write_or_echo_unrelated_configuration(tmp_path):
    settings, mcp, executable, previous, auth = configs(tmp_path)
    before = {p: p.read_bytes() for p in (settings, mcp)}
    result = installer.install(settings, mcp, executable, "home", dry_run=True)
    assert result["dry_run"] and result["added_mcp"]
    assert "opaqueSetting" not in json.dumps(result)
    assert "supermemory-recall" not in json.dumps(result)
    assert not list(tmp_path.glob("*.before-memory-tool-*"))
    assert all(p.read_bytes() == value for p, value in before.items())


@pytest.mark.parametrize("conflict", ["hooks", "mcp"])
def test_conflict_changes_neither_file(tmp_path, conflict):
    settings, mcp, executable, previous, auth = configs(tmp_path)
    if conflict == "hooks":
        previous["hooks"]["Stop"] = [{"hooks": [{"command": "/old/memory-tool hook"}]}]
        settings.write_text(json.dumps(previous))
    else:
        auth["mcpServers"]["memory_tool"] = {"command": "old"}
        mcp.write_text(json.dumps(auth))
    before = {p: p.read_bytes() for p in (settings, mcp)}
    with pytest.raises(ValueError, match="differs"):
        installer.install(settings, mcp, executable, "home")
    assert all(p.read_bytes() == value for p, value in before.items())
    assert not list(tmp_path.glob("*.before-memory-tool-*"))


def test_fresh_install_and_changed_file_guard(tmp_path):
    executable = tmp_path / "memory-tool"
    executable.touch()
    settings, mcp = tmp_path / "new/settings.json", tmp_path / "new/.claude.json"
    installer.install(settings, mcp, executable, "home")
    assert settings.exists() and mcp.exists()
    with pytest.raises(ValueError, match="changed during installation"):
        installer.write_config(settings, b"old contents", {})
    with pytest.raises(ValueError, match="separate files"):
        installer.install(settings, settings, executable, "home")
