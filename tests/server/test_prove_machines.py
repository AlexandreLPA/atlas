"""The rehearsal of scripts/prove_machines.py: its steps run as they do against the user's other
machine over Tailscale, with a real server on this one's loopback in its place (its code naming
`127.0.0.1` as the tailnet address) and the fake model, so the proof cannot break unseen."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from atlas.instances import ConnectionCode, encode_code
from fake_model import FakeModelServer, FakeReply
from server_support import TOKEN, Serve

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def _proof() -> ModuleType:
    spec = importlib.util.spec_from_file_location("prove_machines", SCRIPTS / "prove_machines.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_step_of_the_proof_passes_on_a_server_here(
    serve: Serve,
    fake: FakeModelServer,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    server = serve()
    code = encode_code(ConnectionCode("default", server.port, TOKEN, "127.0.0.1"))
    fake.reply(FakeReply(text="pong"))
    kept = tmp_path / "shop.zip"

    said = _proof().main([code, "--turn", "--backup", str(kept)])

    out = capsys.readouterr().out
    assert said == 0, out
    for number in range(1, 9):
        assert f"\n{number}. " in f"\n{out}"
    assert "'pong'" in out
    assert kept.read_bytes()[:2] == b"PK"
    assert TOKEN not in out, "the code's token is never printed"


def test_a_closed_home_or_a_wrong_code_fails_saying_why(
    serve: Serve, capsys: pytest.CaptureFixture[str]
) -> None:
    proof = _proof()
    server = serve()

    closed = encode_code(ConnectionCode("default", server.port, TOKEN))
    assert proof.main([closed]) == 1
    assert "not open to your machines" in capsys.readouterr().out
    assert proof.main(["hello"]) == 1
    assert "not an Atlas connection code" in capsys.readouterr().out
    wrong = encode_code(ConnectionCode("default", server.port, "x" * 24, "127.0.0.1"))
    assert proof.main([wrong]) == 1
    assert "token was refused" in capsys.readouterr().out
    sys.modules.pop("prove_machines", None)
