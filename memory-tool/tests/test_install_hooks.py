import importlib.util
import json
from pathlib import Path
import tomllib


spec = importlib.util.spec_from_file_location(
    "install_hooks", Path(__file__).parents[1] / "scripts/install_native_hooks.py"
)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def test_install_is_additive_idempotent_and_preserves_trust(tmp_path):
    hooks = tmp_path / "hooks.json"
    previous = {
        "hooks": {
            "SessionStart": [{"hooks": [{"command": "original", "type": "command"}]}]
        }
    }
    hooks.write_text(json.dumps(previous), encoding="utf-8")
    config = tmp_path / "config.toml"
    config.write_text(
        'model = "existing"\n[hooks.state.existing]\ntrusted_hash = "keep-me"\n',
        encoding="utf-8",
    )
    result = installer.install(hooks, "C:/memory-tool.exe")
    assert result["added"] == ["PreCompact", "Stop", "SessionStart"]
    assert installer.install(hooks, "C:/memory-tool.exe")["added"] == []
    assert (
        json.loads(hooks.read_text())["hooks"]["SessionStart"][0]
        == previous["hooks"]["SessionStart"][0]
    )
    installer.context_target(config, 160000, 200000)
    data = tomllib.loads(config.read_text())
    assert data["model"] == "existing"
    assert data["hooks"]["state"]["existing"]["trusted_hash"] == "keep-me"
    assert data["model_auto_compact_token_limit"] == 160000
    assert len(list(tmp_path.glob("*.before-*"))) == 2
