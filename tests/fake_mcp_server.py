"""A tiny MCP server for tests: speaks JSON-RPC over stdin/stdout like a real one."""

import json
import os
import sys

TOOLS = [
    {"name": "get_thing", "description": "Read a thing by key.",
     "inputSchema": {"type": "object", "properties": {
         "key": {"type": "string", "description": "The key"},
         "limit": {"anyOf": [{"type": "integer"}, {"type": "null"}]}},
         "required": ["key"]},
     "annotations": {"readOnlyHint": True}},
    {"name": "make_thing", "description": "Create a thing.",
     "inputSchema": {"type": "object", "properties": {"title": {"type": ["string", "null"]}}}},
    {"name": "nuke", "description": "Delete everything.", "inputSchema": {"type": "object", "properties": {}},
     "annotations": {"destructiveHint": True}},
]

for line in sys.stdin:
    message = json.loads(line)
    if "id" not in message:
        continue  # notifications
    method, params = message["method"], message.get("params") or {}
    print(json.dumps({"jsonrpc": "2.0", "method": "notifications/message", "params": {"data": "log noise"}}), flush=True)
    if method == "initialize":
        result = {"protocolVersion": params.get("protocolVersion"), "capabilities": {"tools": {}},
                  "serverInfo": {"name": "fake", "version": "1"}}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        args = params.get("arguments") or {}
        if args.get("key") == "bad":
            result = {"content": [{"type": "text", "text": "no such thing"}], "isError": True}
        else:
            secret = os.environ.get("FAKE_TOKEN", "")
            result = {"content": [{"type": "text", "text": f"thing {args.get('key')} token-len={len(secret)}"}]}
    else:
        print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32601, "message": "unknown"}}),
              flush=True)
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}), flush=True)
