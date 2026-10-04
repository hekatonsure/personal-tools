import hashlib
import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest


def test_runtime_bridge_is_atomic_guarded_and_preserves_original(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "bridge", Path(__file__).parents[1] / "scripts/bridge_runtime.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    old = tmp_path / "old/site-packages/memory_tool/cli.py"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"def main(): return 1\n")
    new = tmp_path / "new/bin/memory-tool.exe"
    new.parent.mkdir(parents=True)
    new.write_bytes(b"fixture executable")
    digest = hashlib.sha256(new.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="hash"):
        module.install(old, new, "wrong")
    with patch.object(module.subprocess, "run") as run:
        result = module.install(old, new, digest)
        run.assert_called_once_with(
            [str(new.resolve()), "--help"], check=True, capture_output=True, timeout=30
        )
    assert result["hook_trust_modified"] is False
    assert (
        old.with_name("cli.py.before-version-bridge").read_bytes()
        == b"def main(): return 1\n"
    )
    assert "sys.argv[1:]" in old.read_text()
    assert not old.with_name("cli.py.bridge-tmp").exists()
    with pytest.raises(ValueError, match="Already bridged"):
        module.install(old, new, digest)
