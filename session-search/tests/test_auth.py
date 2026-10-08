import json

import pytest

from session_search.jev import typesafe_key


@pytest.fixture
def credentials(tmp_path, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    path = tmp_path / "jevgrep" / "credentials.json"
    path.parent.mkdir()
    return path


def test_saved_typesafe_credentials(credentials):
    credentials.write_text(json.dumps({"provider": "typesafe", "apiKey": " saved-key "}))
    assert typesafe_key() == "saved-key"


def test_environment_takes_precedence(credentials, monkeypatch):
    credentials.write_text("malformed")
    monkeypatch.setenv("TYPESAFE_API_KEY", " environment-key ")
    assert typesafe_key() == "environment-key"


@pytest.mark.parametrize("saved", [
    {}, {"apiKey": "wrong-provider-secret"},
    {"provider": "vercel", "apiKey": "wrong-provider-secret"},
    {"provider": "custom", "apiKey": "wrong-provider-secret"},
    {"provider": "typesafe", "apiKey": " "},
    {"provider": "typesafe", "apiKey": 123}, [],
])
def test_invalid_or_other_provider_credentials_are_not_reused(credentials, saved):
    credentials.write_text(json.dumps(saved))
    with pytest.raises(ValueError, match="jg auth --provider typesafe") as error:
        typesafe_key()
    assert "wrong-provider-secret" not in str(error.value)


@pytest.mark.parametrize("contents", [None, "malformed-private-value"])
def test_missing_or_malformed_credentials_have_actionable_error(credentials, contents):
    if contents is not None:
        credentials.write_text(contents)
    with pytest.raises(ValueError, match="TYPESAFE_API_KEY") as error:
        typesafe_key()
    assert "malformed-private-value" not in str(error.value)


def test_default_config_directory(tmp_path, monkeypatch):
    from pathlib import Path

    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    path = tmp_path / ".config/jevgrep/credentials.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"provider": "typesafe", "apiKey": "default-key"}))
    assert typesafe_key() == "default-key"
