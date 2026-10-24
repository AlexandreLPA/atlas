"""A deterministic Telegram Bot API over real HTTP, for tests.

Tests point the Telegram plugin's `api_url` setting here, so the plugin under proof talks HTTP as it
would to `api.telegram.org`. The server keeps a queue of updates people "send" (`say`, `press`),
hands them out through `getUpdates` as Telegram does (by offset, holding a poll open a moment when
there is nothing), records every call, and answers like Telegram:

- a wrong token is a 401; a scripted error (`fail`) answers a method once, such as a 409 Conflict
  or a 429 with `retry_after`;
- `sendMessage` refuses HTML it cannot parse (`refuse_html`) with Telegram's 400 "can't parse
  entities", and a text past 4096 characters with its 400 "message is too long";
- messages get ids, so buttons can be pressed on them and edited;
- a file someone sent (`send_file`) is found by `getFile` and served at `/file/bot<token>/<path>`;
  past 20 MB, `getFile` answers Telegram's 400 "file is too big".

**Driven from outside**, for the app's end-to-end tests: `python tests/fake_telegram.py` serves
until stopped and writes `FAKE_TELEGRAM_READY port=<n>`; `POST /_fake/say` and `/_fake/press`
send as a person, and `GET /_fake/calls` returns what the bot called.
"""

import argparse
import asyncio
import json
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, cast

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

TOKEN = "123456:fake-token"
#: How a JPEG begins, as Telegram's photos are.
JPEG = b"\xff\xd8\xff\xe0"
#: The largest file `getFile` lets a bot download.
DOWNLOAD_BYTES = 20 * 1024 * 1024
#: How long a poll is held open when nothing waits; short, so tests run fast.
HOLD_SECONDS = 0.2


@dataclass(frozen=True, slots=True)
class Call:
    method: str
    params: dict[str, Any]


class FakeTelegram:
    """Start with `with FakeTelegram() as telegram:`; set the plugin's `api_url` to
    `telegram.url`."""

    def __init__(self, *, token: str = TOKEN, host: str = "127.0.0.1", port: int = 0) -> None:
        self.token = token
        self.calls: list[Call] = []
        self.refuse_html = 0
        self._updates: list[dict[str, Any]] = []
        self._next_update = 1
        self._next_message = 1
        self._failures: deque[tuple[str, int, str, float | None]] = deque()
        #: The files people sent, by id: their bytes, and the size Telegram says.
        self._files: dict[str, tuple[bytes, int]] = {}
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self._host = host
        self.port = port
        self._lock = threading.Lock()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    # -- people ------------------------------------------------------------------------------------

    def say(self, user: int, text: str, *, name: str = "Ana", chat_type: str = "private") -> None:
        """`user` writes `text` to the bot, in their private chat (whose id is theirs)."""
        self._push(
            {
                "message": {
                    "message_id": self._message_id(),
                    "from": {"id": user, "is_bot": False, "first_name": name},
                    "chat": {"id": user, "type": chat_type},
                    "date": int(time.time()),
                    "text": text,
                }
            }
        )

    def send_photo(self, user: int, data: bytes = JPEG + b"photo", caption: str = "") -> None:
        """`user` sends a photo, in two sizes as Telegram sends it, the largest last."""
        self._files["p-small"] = (JPEG + b"small", 10)
        self._files["p-large"] = (data, len(data))
        self._send(
            user,
            {
                "photo": [
                    {"file_id": "p-small", "width": 90, "height": 90, "file_size": 10},
                    {"file_id": "p-large", "width": 1280, "height": 960, "file_size": len(data)},
                ]
            },
            caption,
        )

    def send_file(
        self,
        user: int,
        kind: str,
        data: bytes,
        *,
        name: str | None = None,
        caption: str = "",
        size: int | None = None,
    ) -> None:
        """`user` sends a `document`, `video`, `audio` or `voice`; `size` is what Telegram says
        it weighs, when not its bytes'."""
        file_id = f"f{len(self._files) + 1}"
        self._files[file_id] = (data, len(data) if size is None else size)
        file: dict[str, Any] = {
            "file_id": file_id,
            "file_size": len(data) if size is None else size,
        }
        if name is not None:
            file["file_name"] = name
        self._send(user, {kind: file}, caption)

    def sticker(self, user: int) -> None:
        self._send(user, {"sticker": {"file_id": "s1", "emoji": "🙂"}}, "")

    def _send(self, user: int, fields: dict[str, Any], caption: str) -> None:
        message: dict[str, Any] = {
            "message_id": self._message_id(),
            "from": {"id": user, "is_bot": False, "first_name": "Ana"},
            "chat": {"id": user, "type": "private"},
            "date": int(time.time()),
            **fields,
        }
        if caption:
            message["caption"] = caption
        self._push({"message": message})

    def press(self, user: int, data: str) -> None:
        """`user` presses the button carrying `data`."""
        self._push(
            {
                "callback_query": {
                    "id": f"cb{self._next_update}",
                    "from": {"id": user, "is_bot": False, "first_name": "Ana"},
                    "data": data,
                }
            }
        )

    def fail(
        self, method: str, status: int, description: str, retry_after: float | None = None
    ) -> None:
        """The next call of `method` gets this error."""
        self._failures.append((method, status, description, retry_after))

    # -- what the bot did -------------------------------------------------------------------------

    def called(self, method: str) -> list[dict[str, Any]]:
        return [call.params for call in list(self.calls) if call.method == method]

    def sent(self, chat: int | None = None) -> list[str]:
        """The texts the bot sent, to a chat or to all."""
        return [
            str(params["text"])
            for params in self.called("sendMessage")
            if chat is None or str(params.get("chat_id")) == str(chat)
        ]

    def buttons(self, chat: int) -> list[list[str]]:
        """The buttons' data of each message with buttons the bot sent to `chat`."""
        found: list[list[str]] = []
        for params in self.called("sendMessage"):
            markup = cast("dict[str, Any] | None", params.get("reply_markup"))
            if str(params.get("chat_id")) != str(chat) or not markup:
                continue
            rows = cast("list[list[dict[str, str]]]", markup.get("inline_keyboard") or [])
            found.append([button["callback_data"] for row in rows for button in row])
        return found

    # -- running ----------------------------------------------------------------------------------

    def __enter__(self) -> "FakeTelegram":
        app = Starlette(
            routes=[
                Route("/bot{token}/{method}", self._method, methods=["POST", "GET"]),
                Route("/file/bot{token}/{path:path}", self._file, methods=["GET"]),
                Route("/_fake/say", self._fake_say, methods=["POST"]),
                Route("/_fake/press", self._fake_press, methods=["POST"]),
                Route("/_fake/calls", self._fake_calls, methods=["GET"]),
            ]
        )
        config = uvicorn.Config(
            app,
            host=self._host,
            port=self.port,
            log_config=None,
            log_level=None,
            access_log=False,
            lifespan="off",
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 10
        while not self._server.started:
            if time.monotonic() > deadline or not self._thread.is_alive():
                raise RuntimeError("The fake Telegram did not start.")
            time.sleep(0.01)
        self.port = self._server.servers[0].sockets[0].getsockname()[1]
        return self

    def __exit__(self, *_: object) -> None:
        if self._server is not None and self._thread is not None:
            self._server.should_exit = True
            self._thread.join(timeout=10)

    # -- HTTP -------------------------------------------------------------------------------------

    async def _method(self, request: Request) -> Response:
        method = request.path_params["method"]
        raw = await request.body()
        params = cast("dict[str, Any]", json.loads(raw) if raw else {})
        if request.path_params["token"] != self.token:
            return _error(401, "Unauthorized")
        self.calls.append(Call(method, params))
        for index, (name, status, description, retry_after) in enumerate(self._failures):
            if name == method:
                del self._failures[index]
                return _error(status, description, retry_after)
        if method == "getUpdates":
            return await self._get_updates(params)
        if method == "getFile":
            file_id = str(params.get("file_id"))
            found = self._files.get(file_id)
            if found is None:
                return _error(400, "Bad Request: invalid file_id")
            if found[1] > DOWNLOAD_BYTES:
                return _error(400, "Bad Request: file is too big")
            return _ok({"file_id": file_id, "file_size": found[1], "file_path": f"files/{file_id}"})
        if method == "getMe":
            return _ok({"id": 42, "is_bot": True, "first_name": "Atlas", "username": "atlas_bot"})
        if method == "sendMessage":
            text = str(params.get("text") or "")
            if params.get("parse_mode") == "HTML" and self.refuse_html > 0:
                self.refuse_html -= 1
                return _error(400, "Bad Request: can't parse entities: unclosed tag")
            if len(text) > 4096:
                return _error(400, "Bad Request: message is too long")
            chat = params.get("chat_id")
            return _ok({"message_id": self._message_id(), "chat": {"id": chat}, "text": text})
        return _ok(True)

    async def _file(self, request: Request) -> Response:
        if request.path_params["token"] != self.token:
            return _error(401, "Unauthorized")
        found = self._files.get(str(request.path_params["path"]).removeprefix("files/"))
        return Response(status_code=404) if found is None else Response(found[0])

    async def _get_updates(self, params: dict[str, Any]) -> Response:
        offset = int(params.get("offset") or 0)
        deadline = time.monotonic() + min(float(params.get("timeout") or 0), HOLD_SECONDS)
        while True:
            with self._lock:
                found = [u for u in self._updates if u["update_id"] >= offset]
            if found or time.monotonic() >= deadline:
                return _ok(found)
            await asyncio.sleep(0.02)

    async def _fake_say(self, request: Request) -> Response:
        body = cast("dict[str, Any]", await request.json())
        self.say(int(body["user"]), str(body["text"]), name=str(body.get("name") or "Ana"))
        return _ok(True)

    async def _fake_press(self, request: Request) -> Response:
        body = cast("dict[str, Any]", await request.json())
        self.press(int(body["user"]), str(body["data"]))
        return _ok(True)

    async def _fake_calls(self, request: Request) -> Response:
        return JSONResponse(
            {"calls": [{"method": c.method, "params": c.params} for c in self.calls]}
        )

    def _push(self, update: dict[str, Any]) -> None:
        with self._lock:
            update["update_id"] = self._next_update
            self._next_update += 1
            self._updates.append(update)

    def _message_id(self) -> int:
        with self._lock:
            self._next_message += 1
            return self._next_message


def _ok(result: object) -> Response:
    return JSONResponse({"ok": True, "result": result})


def _error(status: int, description: str, retry_after: float | None = None) -> Response:
    body: dict[str, Any] = {"ok": False, "error_code": status, "description": description}
    if retry_after is not None:
        body["parameters"] = {"retry_after": retry_after}
    return JSONResponse(body, status_code=status)


def main() -> int:
    parser = argparse.ArgumentParser(description="A fake Telegram Bot API, for tests.")
    parser.add_argument("--token", default=TOKEN)
    args = parser.parse_args()
    with FakeTelegram(token=args.token) as telegram:
        sys.stdout.write(f"FAKE_TELEGRAM_READY port={telegram.port}\n")
        sys.stdout.flush()
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            return 0


if __name__ == "__main__":
    sys.exit(main())
