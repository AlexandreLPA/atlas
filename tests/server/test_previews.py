"""A file for the side panel's preview, read on the server's machine (step 15), over the real
server: what it is decides how it shows, and what should not show is refused."""

import base64
from pathlib import Path

from server_support import Serve


def test_a_file_is_read_by_what_it_is_and_refused_when_it_should_not_show(
    serve: Serve, tmp_path: Path
) -> None:
    def at(name: str, content: bytes) -> str:
        path = tmp_path / name
        path.write_bytes(content)
        return str(path)

    with serve().client() as client:

        def read(path: str) -> dict[str, str]:
            return client.result("preview.read", {"path": path})

        assert read(at("page.html", b"<h1>Hi</h1>")) == {"kind": "html", "content": "<h1>Hi</h1>"}
        assert read(at("notes.md", b"# Notes")) == {"kind": "markdown", "content": "# Notes"}
        assert read(at("log.txt", "olá".encode())) == {"kind": "text", "content": "olá"}
        svg = read(at("logo.svg", b'<svg xmlns="http://www.w3.org/2000/svg"/>'))
        assert svg["kind"] == "image"
        assert svg["content"].startswith("data:image/svg+xml;base64,")
        assert base64.b64decode(svg["content"].split(",", 1)[1]).startswith(b"<svg")

        assert read("page.html")["kind"] == "error"
        assert "not a file" in read(str(tmp_path))["content"]
        big = at("big.txt", b"x" * (5 * 1024 * 1024 + 1))
        assert "larger than 5 MB" in read(big)["content"]
