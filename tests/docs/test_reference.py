"""The generated reference is exactly what the code renders: every command, every setting.
Regenerate with `uv run python scripts/gen_docs.py` when a command or a setting changes."""

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import pytest

from atlas.__main__ import build_parser
from atlas.home import Settings

GENERATOR = Path(__file__).resolve().parents[2] / "scripts" / "gen_docs.py"


@pytest.fixture(scope="module")
def generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gen_docs", GENERATOR)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_committed_pages_are_current(generator: ModuleType) -> None:
    for path, render in generator.PAGES.items():
        assert path.exists(), "run scripts/gen_docs.py"
        assert path.read_text(encoding="utf-8") == render(), "run scripts/gen_docs.py"


def test_every_command_is_in_the_commands_page(generator: ModuleType) -> None:
    text: str = generator.render_commands()
    parser = build_parser()

    for path, _, _ in _walk(generator, parser, "atlas"):
        assert f"`{path}`" in text, path


def _walk(generator: ModuleType, parser: object, path: str) -> list[tuple[str, str, object]]:
    found: list[tuple[str, str, object]] = []
    for name, help_text, sub in generator._subcommands(parser):
        found.append((f"{path} {name}", help_text, sub))
        found += _walk(generator, sub, f"{path} {name}")
    return found


def test_every_setting_is_in_the_settings_page_with_its_variable(generator: ModuleType) -> None:
    text: str = generator.render_settings()

    for section in ("server", "log", "vault", "model", "trust", "workspace", "time", "browser"):
        assert f"## [{section}]" in text
    assert "| `approval_timeout` | whole number | `300` | `ATLAS_TRUST_APPROVAL_TIMEOUT` |" in text
    assert '| `host` | text | `"127.0.0.1"` | `ATLAS_SERVER_HOST` |' in text
    assert "*On the settings screen.*" in text
    assert hasattr(Settings, "__dataclass_fields__")


@dataclass(frozen=True, slots=True)
class Undocumented:
    #: Documented.
    told: int = 1
    untold: str = "x"


def test_a_setting_with_no_comment_above_it_is_refused(generator: ModuleType) -> None:
    assert Undocumented.__module__ in sys.modules

    with pytest.raises(ValueError, match=r"\[example\] untold has no `#:` comment"):
        generator.render_section("example", Undocumented, set())


def test_the_check_says_when_a_page_is_stale(
    generator: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stale = tmp_path / "commands.md"
    stale.write_text("old\n", encoding="utf-8")
    monkeypatch.setattr(generator, "PAGES", {stale: generator.render_commands})
    monkeypatch.setattr(generator, "REPOSITORY", tmp_path)

    assert generator.main(["--check"]) == 1
    assert stale.read_text(encoding="utf-8") == "old\n"
    assert generator.main([]) == 0
    assert stale.read_text(encoding="utf-8") == generator.render_commands()
