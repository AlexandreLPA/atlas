"""The rehearsal of scripts/prove_gemini.py: its steps run as they do against the user's own
Gemini CLI, with tests/fake_gemini.py in its place, so the proof cannot break unseen between two
real runs. Step 7 reads Google, which the plugin's own tests stand in for."""

import asyncio
import importlib.util
import json
import sys
import time
from pathlib import Path
from types import ModuleType

import pytest

from gemini_support import FAKE

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _proof() -> ModuleType:
    spec = importlib.util.spec_from_file_location("prove_gemini", SCRIPTS / "prove_gemini.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["prove_gemini"] = module
    spec.loader.exec_module(module)
    return module


def test_every_step_passes_against_the_fake_gemini(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    state = tmp_path / "home" / ".gemini"
    state.mkdir(parents=True)
    (state / "settings.json").write_text(
        json.dumps({"security": {"auth": {"selectedType": "oauth-personal"}}}), encoding="utf-8"
    )
    (state / "google_accounts.json").write_text(
        json.dumps({"active": "user@example.com"}), encoding="utf-8"
    )
    expiry = int((time.time() + 3600) * 1000)
    (state / "oauth_creds.json").write_text(
        json.dumps({"access_token": "t", "expiry_date": expiry}), encoding="utf-8"
    )
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
    monkeypatch.setenv("GEMINI_CLI_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("FAKE_GEMINI_SCRIPT", str(script))
    monkeypatch.delenv("FAKE_GEMINI_UNTRUSTED", raising=False)
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
    assert "yolo: nothing asked" in out
