"""Real providers' answers to one short task, played back through the same adapter and server.

Each file in `tests/recordings/` is what a provider sent to each call of the task's turn, byte for
byte, written by `scripts/record_turn.py`. The fake model serves those bytes in order (`FakeRaw`),
so the real SDK reads them, the real adapter turns them into Atlas's events, and the real loop runs
the real tools on the task's files. What the app received and the conversation reopened are kept
in `apps/desktop/src/store/turns/recorded-*.json`, where `turns.test.ts` checks them as it checks
the six scripted ways: no generic part, no tag in the text, reopened equal to live. A new model is
recorded and runs here with nothing else to write.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from atlas.__main__ import SCHEMAS
from atlas.home import Database, Home, ModelSettings, Settings, WorkspaceSettings, migrate
from atlas.secrets import Vault
from fake_model import FakeModelServer, FakeRaw, FakeReply
from model_support import Connect
from server_support import AtlasServer
from turn_support import ROOT, WORKSPACE, folders, keep, lay_out, normal, run_turn

RECORDINGS = ROOT / "tests" / "recordings"
#: Only the turn's calls carry the main agent's instructions; a title's call has its own.
TURN = "You are Atlas"


@pytest.mark.parametrize("recorded", sorted(RECORDINGS.glob("*.json")), ids=lambda path: path.stem)
def test_a_real_providers_turn_plays_back_as_it_was_recorded(
    recorded: Path,
    home: Home,
    vault: Vault,
    fake: FakeModelServer,
    connect: Connect,
    work: Path,
) -> None:
    recording: dict[str, Any] = json.loads(recorded.read_text(encoding="utf-8"))
    lay_out(work)
    here = json.dumps(str(work))[1:-1]
    replies = [
        FakeRaw(
            reply["body"].replace(WORKSPACE, here), reply["status"], reply["content_type"], TURN
        )
        for reply in recording["replies"]
    ]
    fake.reply(*replies, FakeReply(text='{"title": "Fix the tests"}'))
    migrate(Database(home.database_file), SCHEMAS)
    chosen = ModelSettings(provider=recording["provider"], name=recording["model"])
    models = connect(fake, recording["provider"], settings=chosen)
    settings = Settings(home=home.root, model=chosen, workspace=WorkspaceSettings(work))

    with AtlasServer(home, settings, models, vault) as server:
        events, opened = run_turn(server, recording["task"])

    done = events[-1]["payload"]
    assert (done["status"], done["model_calls"]) == ("answered", len(replies))
    note = (
        f"Recorded from {recording['provider']} {recording['model']} on {recording['recorded_on']}."
    )
    kept = {"note": note, "events": events, "messages": opened["messages"]}
    keep(
        f"recorded-{recorded.stem}", normal(kept, folders(work)), "tests/server/test_recordings.py"
    )
