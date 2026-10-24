"""`desktop_preview` (step 16f): the agent asks the app to show something beside the conversation.
The normalizing of a target and the refusal of a folder."""

import asyncio
import json
from pathlib import Path

from atlas.tools import desktop_preview, normalize_target


def test_a_target_is_normalized_to_an_address_with_its_scheme() -> None:
    assert normalize_target("localhost:3000") == "http://localhost:3000"
    assert normalize_target("127.0.0.1:8080/app") == "http://127.0.0.1:8080/app"
    assert normalize_target("www.example.com") == "https://www.example.com"
    assert normalize_target(" `https://x.dev/a` ") == "https://x.dev/a"
    assert normalize_target("./site/index.html") == "./site/index.html"
    assert normalize_target("C:\\work\\page.html") == "C:\\work\\page.html"
    assert normalize_target("notes") == "notes"


def test_open_shows_a_file_of_the_workspace_and_close_asks_to_close(tmp_path: Path) -> None:
    (tmp_path / "site").mkdir()
    (tmp_path / "page.html").write_text("<p>top</p>", encoding="utf-8")
    page = tmp_path / "site" / "index.html"
    page.write_text("<h1>Hi</h1>", encoding="utf-8")
    shown: list[tuple[str, str, str]] = []
    tool = desktop_preview(lambda *asked: shown.append(asked), tmp_path)

    def call(**arguments: object) -> str:
        async def run() -> object:
            return await tool.run(arguments)

        return str(asyncio.run(run()))

    opened = json.loads(call(action="open", url="site/index.html", label="The page"))
    site = json.loads(call(action="open", url="localhost:5173"))
    closed = json.loads(call(action="close"))
    folder = call(action="open", url="site")
    missing = call(action="open", url="./nothing.html")
    relative = json.loads(call(action="open", url="page.html"))
    empty = call(action="open", url="")
    wrong = call(action="read")

    assert tool.deferred, "behind the tool search, as browser_exec"
    assert opened == {"success": True, "opened": str(page)}
    assert site == {"success": True, "opened": "http://localhost:5173"}
    assert closed == {"success": True, "closed": "all"}
    assert shown == [
        ("open", str(page), "The page"),
        ("open", "http://localhost:5173", ""),
        ("close", "", ""),
        ("open", str(tmp_path / "page.html"), ""),
    ]
    # A name that is a file of the folder is the file, not a domain (normalizing alone
    # would make it https://page.html).
    assert relative == {"success": True, "opened": str(tmp_path / "page.html")}
    assert "a folder cannot be previewed" in folder
    assert "does not exist" in missing
    assert "open needs a url" in empty
    assert "action must be one of: open, close" in wrong
