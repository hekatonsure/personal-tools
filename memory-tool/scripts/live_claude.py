"""Opt-in authenticated Claude probe; never changes organization or auth settings."""

import json
from pathlib import Path
import tempfile

from memory_tool.archive import Archive
from memory_tool.claude import ClaudeGateway


with tempfile.TemporaryDirectory(prefix="memory-tool-claude-probe-") as folder:
    archive = Archive(Path(folder) / "a.sqlite")
    archive.register("claude-probe", folder)
    try:
        answer = ClaudeGateway(archive, "claude-probe", budget=1500, recent=400).turn(
            "Reply with only CLAUDE_PROOF_9271."
        )
        print(json.dumps({"passed": "CLAUDE_PROOF_9271" in answer, "result": answer}))
    except Exception as error:
        print(
            json.dumps(
                {
                    "passed": False,
                    "error": str(error),
                    "input_retained": bool(archive.events("claude-probe")),
                }
            )
        )
    finally:
        archive.close()
