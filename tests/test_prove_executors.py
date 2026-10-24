"""The rehearsal of scripts/prove_executors.py: its steps run as they do against the user's own
Claude Code and a real model, with tests/fake_claude.py and the fake model server in their place,
so the proof cannot break unseen between two real runs (it did in step 16c's second part)."""

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from atlas.home import ModelSettings
from atlas.secrets import Vault
from claude_code_support import FAKE
from fake_model import FakeCall, FakeModelServer, FakeReply
from model_support import Connect

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
PARENT = "Skills come first"
GUARDIAN = "You are a security reviewer"


def _proof() -> ModuleType:
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location("prove_executors", SCRIPTS / "prove_executors.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _script(folder: Path, name: str, script: object) -> None:
    (folder / f"{name}.json").write_text(json.dumps(script), encoding="utf-8")


def test_the_proofs_stop_and_workbench_steps_pass_on_the_fakes(
    fake: FakeModelServer,
    connect: Connect,
    vault: Vault,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", str(scripts))
    monkeypatch.setenv("FAKE_CLAUDE_AUTH", str(tmp_path / "auth.json"))
    _script(scripts, "parar", [{"tool": "Bash", "input": {"command": "pytest"}}, {"result": "x"}])
    _script(scripts, "gemea", [{"result": "Pronto."}])
    question = {"question": "Qual nome?", "options": [{"label": "notas"}, {"label": "ideias"}]}
    _script(
        scripts,
        "bancada",
        {
            "turns": [
                [
                    {"questions": [question]},
                    {"tool": "Write", "input": {"file_path": "notas.txt", "content": "bancada"}},
                    {"sleep": 0.3},
                    {"tool": "Write", "input": {"file_path": "extra.txt", "content": "steer"}},
                    {"result": "Criei notas.txt e extra.txt."},
                ],
                [
                    {"tool": "Write", "input": {"file_path": "auto.txt", "content": "ok"}},
                    {"result": "Criei auto.txt."},
                ],
                [
                    {"tool": "Bash", "input": {"command": "python -m pytest -q"}},
                    {"result": "1 failed."},
                ],
            ]
        },
    )
    root = tmp_path / "proof"
    stop = {
        "executor": "claude-code",
        "tasks": [{"goal": "Run the tests.", "folder": str(root / "work" / "parar")}],
        "approval": "manual",
    }
    calls: list[dict[str, Any]] = [
        {"query": "delegation_search"},
        {"query": "notas"},
    ]
    fake.reply(
        FakeReply(calls=[FakeCall("delegate", json.dumps(stop))], when=PARENT),
        FakeReply(text="Claude Code is on it.", when=PARENT),
        FakeReply(text="It was stopped.", when=PARENT),
        FakeReply(text="APPROVE", when=GUARDIAN),
        FakeReply(calls=[FakeCall("tool_search", json.dumps(calls[0]))], when=PARENT),
        FakeReply(calls=[FakeCall("delegation_search", json.dumps(calls[1]))], when=PARENT),
        FakeReply(text="Ele criou notas.txt, extra.txt e auto.txt.", when=PARENT),
    )
    chosen = ModelSettings(provider="openrouter")
    models = connect(fake, "openrouter", settings=chosen)
    proof = _proof()

    proved = proof.prove(root, models, vault, chosen, FAKE, {5, 7})

    assert proved, "every step did what it should"
    searched = [
        body
        for body in fake.bodies("/v1/chat/completions")
        if any("delegation_search" in str(m.get("content", "")) for m in body["messages"])
    ]
    assert any("notas.txt" in str(body["messages"]) for body in searched), "Atlas read the record"
