"""A fake GitHub over real HTTP, for the marketplace's tests (step 16s).

Tests point `[marketplace] archive_url` and `raw_url` here. The repositories are real git
repositories made in the test, served as GitHub serves them:

- `/archive/{owner}/{repo}/{ref}`: the repository at a branch or a commit, as a `.tar.gz` with one
  top folder, `{repo}-{commit}/`, as GitHub's archives have (made by `git archive`);
- `/raw/{owner}/{repo}/{ref}/{path}`: one file at a branch or a commit; a 404 when it is not there;
- `fail()` answers every request with a 503 until `recover()`, as an outage would; every path asked
  for is recorded (`asked`).

**Driven from outside**, for the app's end-to-end tests: `python tests/fake_github.py` serves until
stopped and writes `FAKE_GITHUB_READY port=<n>`; `POST /_fake/repo` with
`{"name": "owner/repo", "folder": "<a git repository>"}` serves that repository.
"""

import argparse
import gzip
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

GIT = shutil.which("git")


def git(folder: Path, *arguments: str) -> bytes:
    """Run git in `folder` as a test's author; its output, raising when it fails."""
    assert GIT is not None, "the fake GitHub needs git"
    done = subprocess.run(  # noqa: S603 - git, in a folder of the test's
        [GIT, "-c", "user.name=Test", "-c", "user.email=test@example.com", *arguments],
        cwd=folder,
        capture_output=True,
        check=True,
    )
    return done.stdout


class FakeGitHub:
    """Serves git repositories as GitHub's archives and raw files."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self.repositories: dict[str, Path] = {}
        self.asked: list[str] = []
        self.failing = False
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self._host = host
        self.port = port

    def serve(self, name: str, folder: Path) -> None:
        """Serve the git repository in `folder` as `owner/repo`."""
        self.repositories[name] = folder

    def fail(self) -> None:
        self.failing = True

    def recover(self) -> None:
        self.failing = False

    @property
    def archive_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/archive/{{repo}}/{{ref}}"

    @property
    def raw_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/raw/{{repo}}/{{ref}}/{{path}}"

    # -- answering --------------------------------------------------------------------------------

    def _commit(self, name: str, ref: str) -> tuple[Path, str] | None:
        folder = self.repositories.get(name)
        if folder is None:
            return None
        try:
            found = git(folder, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
        except subprocess.CalledProcessError:
            return None
        return folder, found.decode().strip()

    async def _archive(self, request: Request) -> Response:
        self.asked.append(request.url.path)
        if self.failing:
            return Response("unavailable", 503)
        owner, repo, ref = (request.path_params[key] for key in ("owner", "repo", "ref"))
        found = self._commit(f"{owner}/{repo}", ref)
        if found is None:
            return Response("Not Found", 404)
        folder, commit = found
        tar = git(folder, "archive", "--format=tar", f"--prefix={repo}-{commit}/", commit)
        return Response(gzip.compress(tar), media_type="application/x-gzip")

    async def _raw(self, request: Request) -> Response:
        self.asked.append(request.url.path)
        if self.failing:
            return Response("unavailable", 503)
        owner, repo, ref, path = (
            request.path_params[key] for key in ("owner", "repo", "ref", "path")
        )
        found = self._commit(f"{owner}/{repo}", ref)
        if found is None:
            return Response("404: Not Found", 404)
        folder, commit = found
        try:
            data = git(folder, "show", f"{commit}:{path}")
        except subprocess.CalledProcessError:
            return Response("404: Not Found", 404)
        return Response(data, media_type="text/plain; charset=utf-8")

    async def _add(self, request: Request) -> Response:
        body = await request.json()
        self.serve(str(body["name"]), Path(str(body["folder"])))
        return JSONResponse({"ok": True})

    # -- running ----------------------------------------------------------------------------------

    def __enter__(self) -> "FakeGitHub":
        app = Starlette(
            routes=[
                Route("/archive/{owner}/{repo}/{ref}", self._archive),
                Route("/raw/{owner}/{repo}/{ref}/{path:path}", self._raw),
                Route("/_fake/repo", self._add, methods=["POST"]),
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
                raise RuntimeError("The fake GitHub did not start.")
            time.sleep(0.01)
        self.port = self._server.servers[0].sockets[0].getsockname()[1]
        return self

    def __exit__(self, *_: object) -> None:
        if self._server is not None and self._thread is not None:
            self._server.should_exit = True
            self._thread.join(timeout=10)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0)
    port = parser.parse_args().port
    with FakeGitHub(port=port) as server:
        sys.stdout.write(f"FAKE_GITHUB_READY port={server.port}\n")
        sys.stdout.flush()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
