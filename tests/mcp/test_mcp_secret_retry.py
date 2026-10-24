"""A server left out for want of a secret starts once the secret is stored (step 16d).

The real stdio server of `test_mcp.py`, with a `${NOTES_TOKEN}` its `mcp.json` names: it is left out
while the vault lacks the key, and `secret_stored` brings it up with the key the user pasted.
"""

import asyncio
import json
from pathlib import Path
from typing import Any

from atlas.agent import Toolset
from atlas.mcp import McpClient

SERVER = Path(__file__).parent.parent / "mcp_server.py"


def _write(home: Path, servers: dict[str, Any]) -> Path:
    file = home / "mcp.json"
    file.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")
    return file


def test_a_server_missing_its_secret_is_left_out_and_starts_once_it_is_stored(
    tmp_path: Path,
) -> None:
    file = _write(
        tmp_path,
        {
            "notes": {
                "command": "python",
                "args": [str(SERVER)],
                "env": {"NOTES_TOKEN": "${NOTES_TOKEN}"},
            }
        },
    )
    stored: dict[str, str] = {}

    async def scenario() -> tuple[str, bool, bool, str]:
        toolset = Toolset()
        client = McpClient(file, stored.get, toolset)
        await client.start()
        before = client.problems.get("notes", "")
        connected_before = toolset.get("mcp__notes__list_notes") is not None

        # The user pastes the key on the card; the server writes it, then asks the client again.
        stored["NOTES_TOKEN"] = "s3cret"
        await client.secret_stored("NOTES_TOKEN")
        connected_after = toolset.get("mcp__notes__list_notes") is not None
        problem_after = client.problems.get("notes", "")
        await client.close()
        return before, connected_before, connected_after, problem_after

    before, connected_before, connected_after, problem_after = asyncio.run(scenario())

    assert "NOTES_TOKEN" in before
    assert connected_before is False
    assert connected_after is True
    assert problem_after == ""


def test_a_stored_secret_some_other_server_needs_leaves_this_one_alone(tmp_path: Path) -> None:
    file = _write(
        tmp_path,
        {
            "notes": {
                "command": "python",
                "args": [str(SERVER)],
                "env": {"NOTES_TOKEN": "${NOTES_TOKEN}"},
            }
        },
    )

    async def scenario() -> bool:
        toolset = Toolset()
        client = McpClient(file, {}.get, toolset)
        await client.start()
        # Storing another name retries nothing: the notes server still needs NOTES_TOKEN.
        await client.secret_stored("OTHER_KEY")
        connected = toolset.get("mcp__notes__list_notes") is not None
        await client.close()
        return connected

    assert asyncio.run(scenario()) is False


def test_storing_before_any_mcp_file_is_a_no_op(tmp_path: Path) -> None:
    async def scenario() -> None:
        client = McpClient(tmp_path / "mcp.json", {}.get, Toolset())
        await client.secret_stored("NOTES_TOKEN")
        await client.close()

    asyncio.run(scenario())
