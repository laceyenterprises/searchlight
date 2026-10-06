"""Offline MCP startup fixture. No network, credentials, or provider calls."""

import argparse
import json
import sys
import time

parser = argparse.ArgumentParser()
parser.add_argument("--delay", type=float, default=0)
parser.add_argument("--never-initialize", action="store_true")
args = parser.parse_args()
time.sleep(args.delay)
for line in sys.stdin:
    message = json.loads(line)
    if "id" not in message:
        continue
    if message.get("method") == "initialize":
        if args.never_initialize:
            continue
        result = {
            "protocolVersion": "2025-06-18",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "slow-fixture", "version": "1"},
        }
    elif message.get("method") == "tools/list":
        result = {
            "tools": [
                {
                    "name": "search",
                    "description": "offline fixture",
                    "inputSchema": {"type": "object"},
                }
            ]
        }
    else:
        result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}), flush=True)
