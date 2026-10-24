"""The rehearsal of scripts/prove_secrets_tool.py: its steps run as they do against gpt-6-luna, with
the fake model server in its place, so the proof cannot break unseen between two real runs."""

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

from atlas.home import ModelSettings
from atlas.secrets import Vault
from fake_model import FakeCall, FakeModelServer, FakeReply
from model_support import Connect

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _proof() -> ModuleType:
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "prove_secrets_tool", SCRIPTS / "prove_secrets_tool.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_proof_passes_on_the_fake_model(
    fake: FakeModelServer, connect: Connect, vault: Vault, tmp_path: Path
) -> None:
    fake.reply(
        FakeReply(calls=[FakeCall("tool_search", json.dumps({"query": "secrets"}), id="s0")]),
        FakeReply(
            calls=[
                FakeCall(
                    "secrets",
                    json.dumps(
                        {
                            "action": "request",
                            "name": "NOTES_TOKEN",
                            "reason": "to bring up the notes server",
                        }
                    ),
                    id="s1",
                )
            ]
        ),
        FakeReply(text="O servidor de notas subiu."),
    )
    chosen = ModelSettings(provider="openrouter")
    models = connect(fake, "openrouter", settings=chosen)
    proof = _proof()

    assert proof.prove(tmp_path, models, vault, chosen, 0.0) is True
    assert vault.get("NOTES_TOKEN") == proof.FAKE_TOKEN
