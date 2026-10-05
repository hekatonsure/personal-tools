import pytest


@pytest.fixture(autouse=True)
def no_background_labeling(monkeypatch):
    # Stop hooks would otherwise start real labelers that send fixtures to TypeSafe.
    monkeypatch.setenv("MEMORY_TOOL_AUTO_LABEL", "0")
