"""The rehearsal of scripts/prove_deepseek.py: its steps run as they do against the user's own
`dsh`, with tests/fake_dsh.py in its place, so the proof cannot break unseen between two real runs.
Step 7 reads DeepSeek, which the plugin's own tests stand in for."""

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from deepseek_support import FAKE

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _proof() -> ModuleType:
    spec = importlib.util.spec_from_file_location("prove_deepseek", SCRIPTS / "prove_deepseek.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["prove_deepseek"] = module
    spec.loader.exec_module(module)
    return module


def test_every_step_passes_against_the_fake_dsh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    script = tmp_path / "script.json"
    script.write_text(
        json.dumps(
            {
                "prompts": {
                    "allowed.txt": [
                        {"write": "allowed.txt", "content": "proof"},
                        {"say": "written."},
                    ],
                    "refused.txt": [{"write": "refused.txt", "content": "proof"}, {"say": "No."}],
                    "auto.txt": [{"write": "auto.txt", "content": "proof"}, {"say": "written."}],
                    "sleep(20)": [{"command": "sleep 20"}, {"sleep": 20}, {"say": "done."}],
                    "sleep(120)": [{"command": "sleep 120"}, {"sleep": 120}],
                    "created earlier": [{"say": "allowed.txt"}],
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("DSH_HOME", str(tmp_path / "dsh"))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("FAKE_DSH_SCRIPT", str(script))
    proof = _proof()

    code = asyncio.run(proof.prove(json.dumps(FAKE), {1, 2, 3, 4, 5, 6}))

    out = capsys.readouterr().out
    assert code == 0, out
    results = json.loads(out[out.rindex("{") :])
    assert results == {
        "permission": True,
        "refusal": True,
        "auto": True,
        "steer": True,
        "stop": True,
        "session": True,
    }
