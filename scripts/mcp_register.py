"""Point one codebase's agent configuration at the context MCP server.

Reached through `make install`. Writes `.mcp.json` for Claude Code and
`.gemini/settings.json` for the Gemini CLI, which differ only in the key the
address is spelled with. Both files belong to the codebase being onboarded,
not to this repository.

The rule throughout is that nothing existing is overwritten: a file that is
already there gains the `context` server beside whatever else it holds, and a
`context` entry that already names an address is left exactly as it is. A URL
someone chose is a decision, and this script is not the place to reverse it.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

SERVER = "context"


def load(path: str) -> tuple[dict[str, Any] | None, str]:
    """Return the JSON object held in path, and why it could not be read."""
    if not os.path.isfile(path):
        return {}, ""
    try:
        with open(path, encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, ValueError) as error:
        return None, str(error)
    if not isinstance(document, dict):
        return None, "not a JSON object"
    return document, ""


def save(path: str, document: dict[str, Any]) -> None:
    """Write the document back, in the shape prettier would leave it."""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(document, handle, indent=2)
        handle.write("\n")


def register(path: str, entry: dict[str, Any]) -> str:
    """Add the context server to one configuration file, and say what happened."""
    document, error = load(path)
    if document is None:
        return f"left alone ({error})"
    existed = os.path.isfile(path)
    servers = document.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        return "left alone (mcpServers is not an object)"
    current = servers.get(SERVER)
    if isinstance(current, dict):
        address = current.get("url") or current.get("httpUrl") or "an address"
        return f"kept (context already points at {address})"
    servers[SERVER] = entry
    save(path, document)
    return "merged" if existed else "written"


def main() -> None:
    """Register the server in both agents' files for one codebase."""
    if len(sys.argv) != 4:
        print("usage: mcp_register.py ROOT PROJECT PORT", file=sys.stderr)
        raise SystemExit(2)
    root, project, port = sys.argv[1:]
    url = f"http://localhost:{port}/mcp/{project}"
    claude = os.path.join(root, ".mcp.json")
    gemini = os.path.join(root, ".gemini", "settings.json")
    print(f"  .mcp.json               {register(claude, {'type': 'http', 'url': url})}")
    # trust skips the per-call confirmation. Reasonable for a server that only
    # reads a graph of your own code, and unreasonable for one reaching out.
    entry = {"httpUrl": url, "type": "http", "trust": True}
    print(f"  .gemini/settings.json   {register(gemini, entry)}")
    print(f"  address                 {url}")


if __name__ == "__main__":
    main()
