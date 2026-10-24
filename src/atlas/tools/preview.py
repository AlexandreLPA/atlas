"""`desktop_preview`: the agent shows a page, a local site or a file beside the conversation, in
the app's side panel (step 16f).

Decided on 15/10/2026: one tool, `open` and `close`, behind the tool search as `browser_exec` is,
with its normalizing of a target (`localhost:3000` is `http://`, a bare domain `https://`, a path
stays a path) and its refusal of a folder. The preview never opens by itself otherwise (decided
with the user on 15/10/2026): this is the agent asking to show something, and the app never takes
the focus from a conversation in the background for it. There is no `read` action: the agent
reads a file with read_file, and a page with web_fetch or the browser.
"""

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from atlas.agent import Tool

#: Asks the app to show (`open`) or close a target, with a label for its tab.
type Show = Callable[[str, str, str], None]

_DESCRIPTION = (
    "Shows or hides the preview pane next to the chat in the Atlas app. open takes a web URL (a "
    "bare domain works), a localhost dev server or a file path, and an HTML file renders live; "
    "the pane opens in this conversation alone and never pulls the user out of another one. "
    "close hides the tab named by url, or, with no url, every tab of this conversation. Open it "
    "only for something the user would want to look at, such as a page you built or a site you "
    "started."
)
#: This machine's own addresses, served over plain http.
_LOOPBACK = re.compile(r"^(localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])(:\d+)?(/|$)", re.IGNORECASE)
#: A host name with a dot and a top-level domain, an optional port and path after it.
_DOMAIN = re.compile(r"^[\w.-]+\.[a-z]{2,}(:\d+)?(/.*)?$", re.IGNORECASE)
_KEPT_AS_GIVEN = ("/", "./", "../", "~", "file:")


def normalize_target(raw: str) -> str:
    """A bare host as a fetchable URL; a path or a URL with its scheme left as it is."""
    value = raw.strip().strip("`").strip()
    if not value or "://" in value or value.startswith(_KEPT_AS_GIVEN):
        return value
    if _LOOPBACK.match(value):
        return f"http://{value}"
    if _DOMAIN.match(value):
        return f"https://{value}"
    return value


def local_path(target: str) -> Path | None:
    """The file a target names on this machine; `None` for a web address."""
    text = target.strip()
    if not text:
        return None
    if "://" not in text:
        return Path(text).expanduser()
    address = urlparse(text)
    if address.scheme.lower() != "file":
        return None
    location = unquote(address.path or "")
    # A Windows drive arrives as `/C:/...`: the leading slash goes.
    if location[:1] == "/" and location[2:3] == ":":
        location = location[1:]
    return Path(location) if location else None


def desktop_preview(show: Show, workspace: Path) -> Tool:
    """The tool, showing through `show`; a relative path starts from `workspace`."""

    async def run(arguments: dict[str, Any]) -> str:
        action = arguments.get("action")
        raw = arguments.get("url")
        given = raw.strip().strip("`").strip() if isinstance(raw, str) else ""
        # A file of the folder first: normalizing alone takes `page.html` for a bare
        # domain (`https://page.html`), found by this tool's test on 15/10/2026.
        named = workspace / given if given and "://" not in given else None
        target = str(named) if named is not None and named.is_file() else normalize_target(given)
        label = arguments.get("label")
        label = label.strip() if isinstance(label, str) else ""
        if action == "close":
            show("close", target, "")
            return json.dumps({"success": True, "closed": target or "all"})
        if action != "open":
            return "[Atlas: action must be one of: open, close.]"
        if not target:
            return (
                "[Atlas: open needs a url: a web address (https://…), a localhost dev server "
                "or the path of a file to show in the preview pane.]"
            )
        path = local_path(target)
        if path is not None:
            path = path if path.is_absolute() else workspace / path
            if path.is_dir():
                return (
                    f"[Atlas: {path} is a folder, and a folder cannot be previewed. Give a "
                    "file path or a URL instead.]"
                )
            if not path.is_file():
                return f"[Atlas: {path} does not exist.]"
            target = str(path)
        show("open", target, label)
        return json.dumps({"success": True, "opened": target})

    return Tool(
        name="desktop_preview",
        description=_DESCRIPTION,
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["open", "close"]},
                "url": {
                    "type": "string",
                    "description": "open: the target. close: one tab (omit for every tab).",
                },
                "label": {"type": "string", "description": "open: optional tab label."},
            },
            "required": ["action"],
        },
        run=run,
        deferred=True,
    )
