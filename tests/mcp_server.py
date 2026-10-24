"""A real MCP server for the tests, built with the official SDK. Run as a script it speaks stdio;
`build()` gives the same server for streamable HTTP. It keeps notes in memory, so a second call
sees the first only when the session lived between them."""

import os

from mcp.server.mcpserver import MCPServer
from mcp_types import ToolAnnotations


def build() -> MCPServer:
    server = MCPServer("notes", instructions="Notes kept by a test server.")
    notes: list[str] = []

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    def list_notes() -> str:  # pyright: ignore[reportUnusedFunction]
        """List the notes taken so far."""
        return "\n".join(notes) or "no notes"

    @server.tool()
    def add_note(text: str) -> str:  # pyright: ignore[reportUnusedFunction]
        """Add a note."""
        notes.append(text)
        return f"added note {len(notes)}"

    @server.tool()
    def broken() -> str:  # pyright: ignore[reportUnusedFunction]
        """Always fails."""
        raise ValueError("broken on purpose")

    @server.tool(meta={"anthropic/alwaysLoad": True})
    def today() -> str:  # pyright: ignore[reportUnusedFunction]
        """Say today's date, a tool its server asks to keep always on."""
        return "2026-10-09"

    @server.tool()
    def token() -> str:  # pyright: ignore[reportUnusedFunction]
        """Say the token the server was given."""
        return os.environ.get("NOTES_TOKEN", "none")

    return server


if __name__ == "__main__":
    build().run()
