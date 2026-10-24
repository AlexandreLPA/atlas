"""The desktop's TypeScript protocol is exactly what `atlas.server.protocol` renders.
Regenerate with `uv run python scripts/gen_protocol.py` when a shape changes."""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from atlas.server import EVENTS, METHODS, SERVER_REQUESTS

GENERATOR = Path(__file__).resolve().parents[2] / "scripts" / "gen_protocol.py"


@pytest.fixture(scope="module")
def generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gen_protocol", GENERATOR)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_committed_file_is_current(generator: ModuleType) -> None:
    out: Path = generator.OUT
    assert out.exists(), "run scripts/gen_protocol.py"
    assert out.read_text(encoding="utf-8") == generator.render(), "run scripts/gen_protocol.py"


def test_every_method_event_and_request_is_in_it(generator: ModuleType) -> None:
    text: str = generator.render()
    for name in [*METHODS, *EVENTS, *SERVER_REQUESTS]:
        assert f"'{name}'" in text or f"  {name}:" in text, name


def test_a_default_is_optional_only_where_the_client_sends_it(generator: ModuleType) -> None:
    text: str = generator.render()
    # ListParams (a method's params) may leave `limit` out; MessageComplete (an event) has it.
    assert "  limit?: number" in text
    assert "  unsent: string[]" in text
    assert "  unsent?:" not in text
    # ApprovalResult (an answer the client sends) may leave its reason out.
    assert "  reason?: string | null" in text
