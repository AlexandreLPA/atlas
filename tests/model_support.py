"""The types of the model tests' fixtures, which live in tests/models/conftest.py."""

from collections.abc import Awaitable, Callable
from typing import Protocol

from atlas.home import ModelSettings
from atlas.models import CallLedger, Models
from fake_model import FakeModelServer


class Connect(Protocol):
    """Point built-in providers at a fake server, store its key in the vault, and get `Models`."""

    def __call__(
        self,
        server: FakeModelServer | None,
        *providers: str,
        settings: ModelSettings = ...,
        retries: int = ...,
        key: str | None = ...,
        ledger: CallLedger | None = ...,
        silence: float = ...,
    ) -> Models: ...


class Run(Protocol):
    """Run a scenario on a fresh event loop, closing the models' connections in it."""

    def __call__[T](self, models: Models, scenario: Callable[[], Awaitable[T]]) -> T: ...
