"""Explicitly enabled Codex summary calls; no model process starts on import."""

import json
import os
from pathlib import Path
import subprocess
import tempfile

from .codex import isolated_config
from .summary_tree import PROMPT


class CodexSummarizer:
    def __init__(self, model):
        if not model or not model.strip():
            raise ValueError("A summary model must be explicitly selected")
        self.model = model
        self.identity = f"codex-exec:{model}:low:v1"

    def __call__(self, text, limit, *, timeout):
        with tempfile.TemporaryDirectory(prefix="memory-summary-") as directory:
            root = Path(directory)
            instructions = root / "instructions.md"
            instructions.write_text(PROMPT, encoding="utf-8")
            output = root / "summary.txt"
            config = {
                k: v
                for k, v in isolated_config(master=True).items()
                if not k.startswith("mcp_servers.")
            }
            config.update(
                model_instructions_file=str(instructions), project_doc_max_bytes=0
            )
            flags = [
                x for k, v in config.items() for x in ("-c", f"{k}={json.dumps(v)}")
            ]
            command = [
                "codex",
                "exec",
                "--ephemeral",
                "--skip-git-repo-check",
                "--ignore-user-config",
                "--ignore-rules",
                "-s",
                "read-only",
                "-m",
                self.model,
                *flags,
                "--output-last-message",
                str(output),
                "-",
            ]
            # Prevent inherited proxy routing from recursively compacting summary
            # calls. Authentication remains owned by the user's Codex installation.
            env = {
                k: v
                for k, v in os.environ.items()
                if k not in {"OPENAI_BASE_URL", "ANTHROPIC_BASE_URL"}
            }
            subprocess.run(
                command,
                input=f"Limit: {limit} UTF-8 bytes.\nEvidence:\n{text}",
                text=True,
                encoding="utf-8",
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=directory,
                env=env,
                timeout=timeout,
                check=True,
            )
            if output.stat().st_size > limit + 16:
                raise ValueError("Oversized summary output")
            return output.read_text(encoding="utf-8").strip()
