from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .archive import Archive
from .packing import checkpoint


def parser():
    root = argparse.ArgumentParser(prog="memory-tool")
    root.add_argument(
        "--db", help="SQLite path; otherwise MEMORY_TOOL_HOME/memory.sqlite"
    )
    commands = root.add_subparsers(dest="command", required=True)
    hook = commands.add_parser(
        "hook", help="Native Codex lifecycle hook; reads its event from stdin"
    )
    hook.add_argument("--budget", type=int, default=24000)
    hook.add_argument("--recent", type=int, default=8000)
    hook.add_argument(
        "--connectome", help="Optionally bootstrap existing local Connectome history"
    )
    for name in (
        "init",
        "import-connectome",
        "status",
        "pack",
        "search",
        "zoom",
        "chat",
        "reset-codex",
        "serve",
    ):
        command = commands.add_parser(name)
        command.add_argument("--chat", required=True, help="Stable logical chat ID")
        if name in {"init", "import-connectome"}:
            command.add_argument("--project", required=True)
        if name == "import-connectome":
            command.add_argument(
                "--source",
                default=str(Path.home() / ".codex/connectome/history.sqlite"),
            )
            command.add_argument("--session", help="Restrict to one native session")
        if name in {"pack", "chat", "reset-codex"}:
            command.add_argument("--budget", type=int, default=24000)
            command.add_argument("--recent", type=int, default=8000)
        if name == "pack":
            command.add_argument(
                "--session", help="Restore only this native conversation"
            )
            command.add_argument(
                "--focus", help="Optional topic override for memory selection"
            )
            command.add_argument(
                "--output",
                help="Write full history packet to a local file; stdout is metadata",
            )
        if name == "search":
            command.add_argument("query")
        if name in {"search", "chat", "serve"}:
            command.add_argument(
                "--jev",
                action="store_true",
                help="Rank redacted candidate windows directly through configured TypeSafe; opt-in",
            )
        if name == "zoom":
            command.add_argument("event", help="Event ID or note:<id> source reference")
            command.add_argument("--offset", type=int, default=0)
            command.add_argument("--tokens", type=int, default=2000)
        if name == "chat":
            command.add_argument(
                "--backend", choices=["codex", "claude"], default="codex"
            )
            command.add_argument(
                "--model",
                help="Explicit account-supported model; no global setting changes",
            )
            command.add_argument(
                "--worker-write",
                action="store_true",
                help="Permit delegated Codex workers to write in the project sandbox",
            )
            command.add_argument(
                "--message", help="One turn, then exit; otherwise interactive"
            )
            command.add_argument(
                "--mode", choices=["adaptive", "fresh"], default="adaptive"
            )
            command.add_argument("--reset-at", type=int, default=160000)
            command.add_argument("--context-limit", type=int, default=200000)
        if name == "reset-codex":
            command.add_argument("--thread", required=True)
            command.add_argument(
                "--source",
                default=str(Path.home() / ".codex/connectome/history.sqlite"),
            )
        if name == "serve":
            command.add_argument("--delegate-backend", choices=["claude"])
            command.add_argument("--model")
            command.add_argument(
                "--connectome",
                help="Refresh this project from a local Connectome archive on each retrieval",
            )
    return root


def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main():
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = parser().parse_args()
    archive = Archive(args.db)
    gateway = None
    try:
        if args.command == "hook":
            from .hooks import hook_response

            try:
                event = json.load(sys.stdin)
                result = hook_response(
                    archive,
                    event,
                    budget=args.budget,
                    recent=args.recent,
                    connectome=args.connectome,
                )
            except Exception as error:
                result = {
                    "continue": True,
                    "systemMessage": f"memory-tool: invalid hook input ({type(error).__name__})",
                }
            print(json.dumps(result, ensure_ascii=True))
        elif args.command == "init":
            archive.register(args.chat, args.project)
            emit(archive.stats(args.chat))
        elif args.command == "import-connectome":
            emit(
                archive.import_connectome(
                    args.chat, args.source, args.project, args.session
                )
            )
        elif args.command == "status":
            emit(archive.stats(args.chat))
        elif args.command == "pack":
            checkpoint_id, packet = checkpoint(
                archive,
                args.chat,
                budget=args.budget,
                recent_budget=args.recent,
                session=args.session,
                focus=args.focus,
            )
            if args.output:
                Path(args.output).write_text(packet.text, encoding="utf-8")
            emit({"checkpoint": checkpoint_id, **packet.metadata()})
        elif args.command == "search":
            from .retrieval import search

            emit(search(archive, args.chat, args.query, use_jev=args.jev))
        elif args.command == "zoom":
            emit(archive.zoom(args.chat, args.event, args.offset, args.tokens))
        elif args.command == "serve":
            from .mcp import serve

            serve(
                archive,
                args.chat,
                args.delegate_backend,
                args.model,
                args.jev,
                args.connectome,
            )
        elif args.command == "reset-codex":
            from .native import reset_desktop

            emit(
                reset_desktop(
                    archive,
                    args.chat,
                    args.thread,
                    args.source,
                    args.budget,
                    args.recent,
                )
            )
        elif args.command == "chat":
            if args.backend == "codex":
                from .codex import CodexGateway

                gateway = CodexGateway(
                    archive,
                    args.chat,
                    args.model,
                    args.budget,
                    args.recent,
                    worker_write=args.worker_write,
                    mode=args.mode,
                    reset_at=args.reset_at,
                    context_limit=args.context_limit,
                    jev=args.jev,
                )
            else:
                from .claude import ClaudeGateway

                gateway = ClaudeGateway(
                    archive, args.chat, args.model, args.budget, args.recent
                )
            if args.message is not None:
                print(gateway.turn(args.message))
            else:
                print(
                    "Durable chat ready. /quit exits; /status shows metadata. Codex reuses context until the configured reset threshold."
                )
                while True:
                    try:
                        prompt = input("you> ")
                    except EOFError:
                        break
                    if prompt == "/quit":
                        break
                    if prompt == "/status":
                        emit(archive.stats(args.chat))
                    elif prompt == "/reset":
                        if hasattr(gateway, "close"):
                            gateway.close()
                            print(
                                "Working context closed. The next message restores durable memory."
                            )
                    elif prompt.strip():
                        try:
                            print(gateway.turn(prompt))
                        except Exception as error:
                            print(
                                f"Turn failed; archived input retained: {error}",
                                file=sys.stderr,
                            )
    except (Exception, KeyboardInterrupt) as error:
        print(f"memory-tool: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    finally:
        if gateway is not None and hasattr(gateway, "close"):
            gateway.close()
        archive.close()


if __name__ == "__main__":
    main()
