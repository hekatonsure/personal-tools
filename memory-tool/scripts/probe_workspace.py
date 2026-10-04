"""Owned probe workspace with normal directory inheritance, never user source files."""

from contextlib import contextmanager
from pathlib import Path
import shutil
import uuid


@contextmanager
def workspace(prefix="memory-tool-probe"):
    root = (Path.cwd() / "private").resolve()
    root.mkdir(exist_ok=True)
    project = root / (prefix + "-" + uuid.uuid4().hex)
    # Python 3.13's Windows mkdir(0o700), used by tempfile, changes inheritance.
    # Native sandbox workers have a different identity; use ordinary project ACLs.
    project.mkdir()
    try:
        yield project
    finally:
        resolved = project.resolve()
        if resolved.parent != root or project.is_symlink() or project.is_junction():
            raise RuntimeError("Probe cleanup target changed; refusing deletion")
        shutil.rmtree(resolved)
