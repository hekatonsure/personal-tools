"""Read-only check that the CLI proxy can see the desktop's owning server."""

import argparse
import json

from memory_tool.rpc import CodexRpc


parser = argparse.ArgumentParser()
parser.add_argument("thread")
args = parser.parse_args()
try:
    with CodexRpc(proxy=True, timeout=15) as rpc:
        thread = rpc.request(
            "thread/read", {"threadId": args.thread, "includeTurns": False}
        )["thread"]
        print(
            json.dumps(
                {
                    "connected": True,
                    "thread": thread["id"],
                    "status": thread.get("status"),
                }
            )
        )
except Exception as error:
    print(json.dumps({"connected": False, "error": str(error)}))
