"""A small HTTP server over real sockets, for the web tools' tests: it serves the routes a test
gives it and records each request."""

import threading
import time
from dataclasses import dataclass
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route
from starlette.types import ASGIApp


@dataclass
class Seen:
    method: str
    path: str
    query: dict[str, str]
    headers: dict[str, str]
    body: bytes


class LocalServer:
    """`with LocalServer({"/page": handler}) as server:`; `server.url("/page")`."""

    def __init__(self, routes: dict[str, Any]) -> None:
        self.seen: list[Seen] = []
        self._routes = routes
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self.port = 0

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def __enter__(self) -> "LocalServer":
        def recorded(handler: Any) -> Any:
            async def endpoint(request: Request) -> Response:
                self.seen.append(
                    Seen(
                        request.method,
                        request.url.path,
                        dict(request.query_params),
                        dict(request.headers),
                        await request.body(),
                    )
                )
                return await handler(request)

            return endpoint

        app: ASGIApp = Starlette(
            routes=[
                Route(path, recorded(handler), methods=["GET", "POST"])
                for path, handler in self._routes.items()
            ]
        )
        config = uvicorn.Config(
            app, host="127.0.0.1", port=0, log_config=None, log_level=None, lifespan="off"
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 10
        while not self._server.started:
            if time.monotonic() > deadline or not self._thread.is_alive():
                raise RuntimeError("The local server did not start.")
            time.sleep(0.01)
        self.port = self._server.servers[0].sockets[0].getsockname()[1]
        return self

    def __exit__(self, *_: object) -> None:
        if self._server is not None and self._thread is not None:
            self._server.should_exit = True
            self._thread.join(timeout=10)
