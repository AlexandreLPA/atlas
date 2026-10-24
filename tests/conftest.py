"""What every test shares.

Two guards: no test touches a live home, and logging is put back as it was. And the pieces the
model and agent tests run on: a temporary home with a real vault, and the fake model server.
`connect` points built-in providers at a fake server through the user's real `models.toml`, so each
provider keeps its real quirks and catalog data and only its address changes, and it stores the
fake's key in the real vault, where the adapter must find it.
"""

import asyncio
import logging
import os
import sys
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path

import pytest

from atlas.home import Home, ModelSettings, Redactor, VaultSettings, open_home
from atlas.models import SILENCE_LIMIT, CallLedger, Models, Wire, load_catalog
from atlas.plugins import LoadedExecutor, Plugins
from atlas.secrets import Vault
from fake_model import FakeModelServer
from model_support import Connect, Run


@pytest.fixture(autouse=True)
def _no_live_home(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Point the process's ATLAS_HOME at a temporary folder, in case a test reaches os.environ;
    and Claude Code's own folder too, so no test lists or reads the user's sessions (step 16f);
    and the user's home folder, beside which another account's Claude Code folder is made (step
    16h), so no test makes one in the user's own."""
    monkeypatch.setenv("ATLAS_HOME", str(tmp_path_factory.mktemp("process-home")))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path_factory.mktemp("claude-home")))
    # Only this process's `Path.home()`: a program the tests start keeps the real one (Chrome on
    # Windows opens no DevTools port without it), and a test that names a home of its own in the
    # environment gets that one.
    real, user = Path.home(), tmp_path_factory.mktemp("user-home")
    named = "USERPROFILE" if sys.platform == "win32" else "HOME"

    def home(_cls: type[Path]) -> Path:
        given = os.environ.get(named)
        return Path(given) if given and Path(given) != real else user

    monkeypatch.setattr(Path, "home", classmethod(home))
    expand = Path.expanduser

    def expanduser(self: Path) -> Path:
        # `~` is the same home as `Path.home()`.
        if self.parts and self.parts[0] == "~":
            return Path.home().joinpath(*self.parts[1:])
        return expand(self)

    monkeypatch.setattr(Path, "expanduser", expanduser)
    monkeypatch.delenv("ATLAS_VAULT_KEY", raising=False)
    monkeypatch.delenv("ATLAS_VAULT_KEY_FILE", raising=False)


@pytest.fixture(autouse=True)
def _restore_logging() -> Iterator[None]:
    """The entry point configures the root logger; each test gets the previous one back."""
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    for handler in list(root.handlers):
        if handler not in handlers:
            root.removeHandler(handler)
            handler.close()
    for handler in handlers:
        if handler not in root.handlers:
            root.addHandler(handler)
    root.setLevel(level)


# -- the model and agent tests ------------------------------------------------------------------


@pytest.fixture
def home(tmp_path_factory: pytest.TempPathFactory) -> Home:
    return open_home(tmp_path_factory.mktemp("atlas") / "home")


@pytest.fixture
def vault(home: Home) -> Vault:
    return Vault(home.secrets_folder, VaultSettings(), Redactor())


@pytest.fixture
def fake() -> Iterator[FakeModelServer]:
    with FakeModelServer() as server:
        yield server


@pytest.fixture
def connect(home: Home, vault: Vault) -> Connect:
    def connect(
        server: FakeModelServer | None,
        *providers: str,
        settings: ModelSettings = ModelSettings(),  # noqa: B008
        retries: int = 0,
        key: str | None = "fake-key",
        ledger: CallLedger | None = None,
        silence: float = SILENCE_LIMIT,
    ) -> Models:
        address = server.base_url if server is not None else "http://127.0.0.1:9/v1"
        shipped = load_catalog(home.models_file)

        def where(name: str) -> str:
            # Anthropic's SDK adds /v1/messages itself, so its address is the server's root.
            messages = name in shipped.providers and shipped.provider(name).wire is Wire.MESSAGES
            return address.removesuffix("/v1") if messages else address

        existing = home.models_file.read_text("utf-8") if home.models_file.exists() else ""
        home.models_file.write_text(
            existing
            + "".join(f'[providers.{name}]\nbase_url = "{where(name)}"\n\n' for name in providers),
            encoding="utf-8",
        )
        catalog = load_catalog(home.models_file)
        for name in providers:
            secret = catalog.provider(name).key
            if secret is not None and key is not None:
                vault.set(secret, key)
        return Models(catalog, vault, settings, ledger=ledger, retries=retries, silence=silence)

    return connect


@pytest.fixture
def run() -> Run:
    def run[T](models: Models, scenario: Callable[[], Awaitable[T]]) -> T:
        """Run a scenario on a fresh event loop, and close the models' connections in it."""

        async def main() -> T:
            try:
                return await scenario()
            finally:
                await models.close()

        return asyncio.run(main())

    return run


@pytest.fixture(autouse=True)
def no_offered_executors(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """At a real start Atlas looks for the harnesses of the plugins it ships even while they are
    off (step 16c), which would run the `claude` installed on this machine in every server test.
    A test marked `offered_executors` looks, on the `program` its home names."""
    if request.node.get_closest_marker("offered_executors") is not None:
        return

    async def none(_plugins: Plugins) -> list[LoadedExecutor]:
        return []

    monkeypatch.setattr(Plugins, "offered_executors", none)


@pytest.fixture(autouse=True)
def no_installed_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    """No browser unless a test gives one (the assistant's `chrome`): a Chrome installed on the
    machine would otherwise change the tools a model sees."""
    monkeypatch.setattr("atlas.assistant.service._installed_browser", lambda: None)
