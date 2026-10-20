"""``python -m deckdna.mcp`` — MCP-сервер по stdio (для агентов)."""

from deckdna.mcp.server import build_server

if __name__ == "__main__":
    build_server().run("stdio")
