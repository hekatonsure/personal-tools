"""Check delegated workspace writes without touching user source files."""

from contextlib import ExitStack
import json
from probe_workspace import workspace

from memory_tool.archive import Archive
from memory_tool.codex import CodexGateway


with workspace("write-probe") as project, ExitStack() as cleanup:
    archive = Archive(project / "a.sqlite")
    cleanup.callback(archive.close)
    archive.register("write-probe", str(project))
    gateway = CodexGateway(
        archive,
        "write-probe",
        model="gpt-5.6-luna",
        budget=1500,
        recent=400,
        mode="fresh",
        worker_write=True,
    )
    cleanup.callback(gateway.close)
    answer = gateway.turn(
        "Delegate creation of a regular UTF-8 text file worker-output.txt containing exactly WORKER_WRITE_PROOF_5831=maple-22. Confirm the worker verified its contents."
    )
    target = project / "worker-output.txt"
    metadata = {
        "is_file": target.is_file(),
        "is_directory": target.is_dir(),
        "worker_calls": gateway.worker_calls,
        "answer": answer,
    }
    print(json.dumps(metadata), flush=True)
    commands = [
        json.loads(e["text"]).get("command")
        for e in archive.events("write-probe")
        if e["role"] == "tool_result"
        and json.loads(e["text"]).get("type") == "commandExecution"
    ]
    print(
        json.dumps({"diagnostic_project": str(project), "commands": commands}),
        flush=True,
    )
    assert target.is_file(), metadata
    value = target.read_text(encoding="utf-8-sig").strip()
    assert value == "WORKER_WRITE_PROOF_5831=maple-22", value
    print(json.dumps({"passed": True, "scope": "disposable workspace directory"}))
