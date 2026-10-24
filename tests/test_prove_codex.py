"""The rehearsal of scripts/prove_codex.py: its steps run as they do against the user's own Codex,
with tests/fake_codex.py in its place, so the proof cannot break unseen between two real runs."""

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from codex_support import FAKE

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _proof() -> ModuleType:
    spec = importlib.util.spec_from_file_location("prove_codex", SCRIPTS / "prove_codex.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["prove_codex"] = module
    spec.loader.exec_module(module)
    return module


def test_every_step_passes_against_the_fake_codex(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / ".codex"
    home.mkdir()
    script = tmp_path / "script.json"
    write = {"write": "", "content": "proof"}
    script.write_text(
        json.dumps(
            {
                "prompts": {
                    "allowed.txt": [
                        {**write, "write": "../outside/allowed.txt"},
                        {"say": "written."},
                    ],
                    "refused.txt": [{**write, "write": "../outside/refused.txt"}, {"say": "No."}],
                    "reviewed.txt": [{**write, "write": "../outside/reviewed.txt"}, {"say": "ok"}],
                    "sleep(15)": [{"command": "sleep 15"}, {"sleep": 1}, {"say": "done."}],
                    "sleep(120)": [{"command": "sleep 120"}, {"sleep": 120}],
                    "created earlier": [{"say": "allowed.txt"}],
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setenv("FAKE_CODEX_SCRIPT", str(script))
    proof = _proof()

    code = asyncio.run(proof.prove(json.dumps(FAKE), {1, 2, 3, 4, 5, 6}))

    out = capsys.readouterr().out
    assert code == 0, out
    results = json.loads(out[out.rindex("{") :])
    assert results == {
        "permission": True,
        "refusal": True,
        "reviewer": True,
        "steer": True,
        "stop": True,
        "session": True,
    }
