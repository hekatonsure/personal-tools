"""Desktop compaction is only allowed after a complete, idle source snapshot."""

from pathlib import Path
import sqlite3

from .archive import canonical
from .codex import compact_and_restore
from .rpc import CodexRpc


def verify_capture(source, thread, project):
    path = Path(source)
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        session = connection.execute(
            "SELECT project FROM sessions WHERE id=?", (thread,)
        ).fetchone()
        if not session or canonical(session[0]) != canonical(project):
            raise ValueError(
                "Target thread is not captured in this project's Connectome archive"
            )
        cursors = connection.execute(
            "SELECT path,offset FROM cursors WHERE session=?", (thread,)
        ).fetchall()
        if not cursors:
            raise ValueError("No source cursor; refusing compaction")
        for name, offset in cursors:
            current = Path(name).stat().st_size
            if current != offset:
                raise ValueError(
                    f"Capture is not current ({current - offset} source bytes pending); retry after observer catches up"
                )
        return [(name, offset) for name, offset in cursors]
    finally:
        connection.close()


def reset_desktop(archive, chat, thread, source, budget=24000, recent=8000):
    project = archive.project(chat)
    snapshot = verify_capture(source, thread, project)
    archive.import_connectome(chat, source, project, thread)
    # Use the existing owning server. Never resume/edit a running desktop rollout in a second server.
    with CodexRpc(proxy=True) as rpc:
        if verify_capture(source, thread, project) != snapshot:
            raise ValueError(
                "Transcript changed while preparing the checkpoint; retry when idle"
            )
        return compact_and_restore(rpc, archive, chat, thread, budget, recent)
