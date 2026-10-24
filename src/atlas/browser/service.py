"""The browser: one Chrome per mode, opened when a conversation first needs it, and the code of
each call run through the engine in the conversation's own session.

Decided with the user on 12/10/2026 (docs/ARCHITECTURE.md, Browser). A call, in order:

1. The addresses written in the code are checked: a cloud metadata address, by name, by number or
   by what the name resolves to, is refused, and so is an address carrying a secret Atlas knows.
   A private address is allowed: the browser is local.
2. The code runs, the conversation's tab pinned first; at its time limit its whole process tree
   is stopped.
3. The output is redacted, the error output cut to its last 4,000 characters, and the screenshot
   it printed, if any, goes with it.
"""

import asyncio
import hashlib
import json
import logging
import os
import sys
import tempfile
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from atlas.agent import Tool, ToolOutput
from atlas.browser.errors import BrowserError
from atlas.browser.model import (
    METADATA_REFUSED,
    OWN_TAB_PREAMBLE,
    PARAMETERS,
    STDERR_CHARS,
    TOOL_NAME,
    VAULT_URL_REFUSED,
    BrowserMode,
    description,
    engine_environment,
    host_of,
    metadata_address,
    metadata_host,
    printed_images,
    session_name,
    timed_out,
    timeout_of,
    urls_in,
)
from atlas.browser.store import (
    ENGINE,
    Chrome,
    close_chrome,
    listening,
    open_chrome,
    read_image,
    remove_profile,
    run_engine,
    stop_engine,
)
from atlas.models import Image
from atlas.trust import untrusted

#: How long a name may take to resolve before the call goes on without it.
RESOLVE_SECONDS = 2.0
#: The largest screenshot handed to the model, as read_file's largest image.
IMAGE_BYTES = 5 * 1024 * 1024
_MEDIA_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".webp": "image/webp"}  # fmt: skip

_logger = logging.getLogger("atlas.browser")


@dataclass(frozen=True, slots=True)
class BrowserResult:
    """A call's result for the model, and the screenshot it printed."""

    text: str
    images: tuple[Image, ...] = ()


#: Resolves a host name to its addresses.
type Resolve = Callable[[str], Awaitable[list[str]]]


async def _resolve(host: str) -> list[str]:
    try:
        async with asyncio.timeout(RESOLVE_SECONDS):
            found = await asyncio.get_running_loop().getaddrinfo(host, None)
    except (OSError, TimeoutError, UnicodeError):
        return []
    return [str(entry[4][0]) for entry in found]


class Browser:
    """The browsers of one Atlas, and the conversations that drive them."""

    def __init__(
        self,
        folder: Path,
        chrome: Sequence[str],
        *,
        redact: Callable[[str], str],
        engine: Sequence[str] = ENGINE,
        resolve: Resolve = _resolve,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        """`folder` holds the profile, the engine's files and the workspaces; `chrome` is the
        browser's program and the arguments before its own; `redact` takes Atlas's secrets out
        of a text."""
        self._folder = folder
        self._chrome = list(chrome)
        self._redact = redact
        self._engine = list(engine)
        self._resolve = resolve
        self._environ = dict(os.environ if environ is None else environ)
        self._open: dict[BrowserMode, Chrome] = {}
        self._starting: dict[BrowserMode, asyncio.Lock] = {}
        self._sessions: dict[BrowserMode, set[str]] = {mode: set() for mode in BrowserMode}

    def tool(self, conversation_id: str, *, sees_images: bool) -> Tool:
        """`browser_exec` for a conversation, deferred behind tool search."""

        async def run(arguments: dict[str, Any]) -> str | ToolOutput:
            code = arguments.get("code")
            if not isinstance(code, str) or not code.strip():
                return (
                    "No code provided. Pass Python that uses the pre-imported helpers, e.g. "
                    'new_tab("https://example.com") then print(page_info()).'
                )
            try:
                mode = BrowserMode(arguments.get("mode") or BrowserMode.PROFILE)
            except ValueError:
                return f"Unknown mode {arguments.get('mode')!r}: use profile, clean or headless."
            result = await self.run(
                conversation_id, code, mode, timeout_of(arguments.get("timeout_s"))
            )
            return ToolOutput(result.text, images=result.images) if result.images else result.text

        return Tool(
            TOOL_NAME,
            description(sees_images),
            PARAMETERS,
            run,
            failure_tolerant=True,
            deferred=True,
        )

    # -- a call ------------------------------------------------------------------------------------

    async def run(
        self, conversation_id: str, code: str, mode: BrowserMode, seconds: int
    ) -> BrowserResult:
        """Run a call's code in the conversation's session of the `mode`'s browser."""
        refused = await self._refused(code)
        if refused is not None:
            return BrowserResult(json.dumps({"success": False, "error": refused}))
        session = session_name(conversation_id, mode)
        started = time.time()
        try:
            chrome = await self._chrome_for(mode)
            self._sessions[mode].add(conversation_id)
            environment = self._environment(chrome, session)
            ran = await run_engine(self._engine, OWN_TAB_PREAMBLE + code, environment, seconds)
        except TimeoutError:
            return BrowserResult(json.dumps({"success": False, "error": timed_out(seconds)}))
        except BrowserError as error:
            return BrowserResult(json.dumps({"success": False, "error": str(error)}))
        result: dict[str, object] = {
            "success": ran.exit_code == 0,
            "exit_code": ran.exit_code,
            "output": self._redact(ran.output),
        }
        errors = self._redact(ran.errors.strip())
        if errors:
            result["stderr"] = (
                errors if len(errors) <= STDERR_CHARS else "… " + errors[-STDERR_CHARS:]
            )
        text = untrusted(TOOL_NAME, json.dumps(result, ensure_ascii=False))
        return BrowserResult(text, self._screenshot(ran.output, started))

    async def _refused(self, code: str) -> str | None:
        """Why the code's addresses may not be reached, or `None`."""
        for url in urls_in(code):
            if self._redact(url) != url:
                return VAULT_URL_REFUSED
            host = host_of(url)
            if not host:
                continue
            if metadata_host(host):
                return METADATA_REFUSED
            if any(metadata_address(address) for address in await self._resolve(host)):
                return METADATA_REFUSED
        return None

    def _screenshot(self, output: str, started: float) -> tuple[Image, ...]:
        for printed in printed_images(output):
            path = Path(printed)
            data = read_image(path, started, IMAGE_BYTES)
            if data is not None:
                return (Image(_MEDIA_TYPES.get(path.suffix.lower(), "image/png"), data),)
        return ()

    def _environment(self, chrome: Chrome, session: str) -> dict[str, str]:
        workspace = self._folder / "workspaces" / session
        workspace.mkdir(parents=True, exist_ok=True)
        return engine_environment(
            self._environ,
            home=str(self._folder / "engine"),
            workspace=str(workspace),
            session=session,
            endpoint=chrome.endpoint,
            runtime=_socket_folder(self._folder, self._environ),
        )

    async def _chrome_for(self, mode: BrowserMode) -> Chrome:
        """The mode's Chrome, opened again when the user closed it, with the engine's sessions on
        it stopped: each holds the endpoint it started with."""
        lock = self._starting.setdefault(mode, asyncio.Lock())
        async with lock:
            found = self._open.get(mode)
            if found is not None and await listening(found.endpoint):
                return found
            if found is not None:
                _logger.info("The browser was closed; it opens again.", extra={"mode": mode.value})
                await self._stop_sessions(mode, found)
                if not mode.kept:
                    remove_profile(found.profile)
            profile = self._profile(mode)
            if not mode.kept:
                remove_profile(profile)
            chrome = await open_chrome(self._chrome, profile, headless=not mode.windowed)
            self._open[mode] = chrome
            return chrome

    def _profile(self, mode: BrowserMode) -> Path:
        if mode.kept:
            return self._folder / "profile"
        return self._folder / "temporary" / mode.value

    async def _stop_sessions(self, mode: BrowserMode, chrome: Chrome) -> None:
        for conversation_id in self._sessions[mode]:
            session = session_name(conversation_id, mode)
            await stop_engine(self._engine, self._environment(chrome, session))
        self._sessions[mode].clear()

    # -- the end -----------------------------------------------------------------------------------

    async def close(self) -> None:
        """Stop every session and close every browser; the temporary profiles go with them."""
        for mode, chrome in list(self._open.items()):
            await self._stop_sessions(mode, chrome)
            await close_chrome(chrome)
            if not mode.kept:
                remove_profile(chrome.profile)
        self._open.clear()


def _socket_folder(folder: Path, environ: Mapping[str, str]) -> str | None:
    """Where the engine's daemon puts its Unix socket: a short private folder, since a socket's
    address may hold at most 108 bytes on Linux (104 on macOS), and the home's own `runtime/` under
    a long home or a named one passed that (found by CI on 13/10/2026: "AF_UNIX path too long").
    Atlas's own choice over the engine's default. `None` on Windows, which uses a port."""
    if sys.platform == "win32":
        return None
    base = environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    mark = hashlib.sha256(str(folder.resolve()).encode("utf-8")).hexdigest()[:12]
    return str(Path(base) / f"atlas-{os.getuid()}" / mark)
