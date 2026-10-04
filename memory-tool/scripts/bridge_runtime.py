"""Upgrade an approved launcher without replacing a running Windows executable.

The old uv entry point imports memory_tool.cli.main on each NEW process. Replace
only that Python module with a fixed argv-preserving dispatch to a tested new
runtime. Existing processes keep their loaded code until naturally reconnected.
No hook definitions, hashes, trust records or Codex permission settings are edited.
"""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def install(
    old_module, new_executable, expected_sha256, expected_old_module_sha256=None
):
    old_module, new_executable = (
        Path(old_module).resolve(),
        Path(new_executable).resolve(),
    )
    if (
        not old_module.is_file()
        or old_module.name != "cli.py"
        or old_module.parent.name != "memory_tool"
    ):
        raise ValueError("Expected the installed memory_tool/cli.py entry module")
    if not new_executable.is_file():
        raise ValueError("New launcher does not exist")
    if old_module.parents[2] in new_executable.parents:
        raise ValueError("Target must be a separate runtime")
    if hashlib.sha256(new_executable.read_bytes()).hexdigest() != expected_sha256:
        raise ValueError("New executable hash differs from tested launcher")
    original = old_module.read_bytes()
    retarget = b"MEMORY_TOOL_VERSION_BRIDGE" in original
    if retarget and (
        not expected_old_module_sha256
        or hashlib.sha256(original).hexdigest() != expected_old_module_sha256
    ):
        raise ValueError(
            "Already bridged; review the existing target before changing it"
        )
    # Verify the NEW launcher before altering the compatibility entry point.
    subprocess.run(
        [str(new_executable), "--help"], check=True, capture_output=True, timeout=30
    )
    saved = old_module.with_name("cli.py.before-version-bridge")
    if saved.exists() and not retarget:
        raise ValueError("Backup already exists; refusing to overwrite it")
    if not retarget:
        saved.write_bytes(original)
    elif not saved.exists():
        raise ValueError("Original backup is missing")
    text = f"""# MEMORY_TOOL_VERSION_BRIDGE: new processes only; original beside this file.
import subprocess
import sys

def main():
    raise SystemExit(subprocess.call([{str(new_executable)!r}, *sys.argv[1:]]))
"""
    temporary = old_module.with_name("cli.py.bridge-tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(old_module)
    return {
        "old_entry_module": str(old_module),
        "new_executable": str(new_executable),
        "new_executable_sha256": expected_sha256,
        "backup_sha256": hashlib.sha256(saved.read_bytes()).hexdigest(),
        "new_processes_only": True,
        "hook_trust_modified": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--old-module", type=Path, required=True)
    parser.add_argument("--new-executable", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument(
        "--expected-old-module-sha256",
        help="Explicitly reviewed bridge hash, required for retargeting",
    )
    args = parser.parse_args()
    print(
        json.dumps(
            install(
                args.old_module,
                args.new_executable,
                args.expected_sha256,
                args.expected_old_module_sha256,
            ),
            indent=2,
        )
    )
