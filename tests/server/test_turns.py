"""The six ways a model behaves in a turn, from step 9c's prototype, through the real server.

Each script in `tests/turns/` (the fake model's replies, as the desktop's end-to-end tests write
them) runs as a conversation's turn on a real server, through the real `chat` adapter, loop and
tools. What the app received and the conversation reopened are kept, made the same on every machine
(the working folder a marker, the times zero, no cost), in `apps/desktop/src/store/turns/`, which
the app's tests replay through the screen's reducer (`turns.test.ts`): the parts, their states and
the interim text, live and reopened. A change in what the server says fails here first; when it is
meant, write the files again with `ATLAS_WRITE_TURNS=1 uv run pytest tests/server/test_turns.py`.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from fake_model import FakeModelServer, reply_from_json
from server_support import Serve
from turn_support import ROOT, folders, keep, lay_out, normal, run_turn

SCRIPTS = ROOT / "tests" / "turns"


@pytest.mark.parametrize("script", sorted(SCRIPTS.glob("*.json")), ids=lambda path: path.stem)
def test_a_way_of_a_model_reaches_the_app_as_kept(
    script: Path, serve: Serve, fake: FakeModelServer, work: Path
) -> None:
    spec: dict[str, Any] = json.loads(script.read_text(encoding="utf-8"))
    lay_out(work)
    fake.chunk_size = int(spec.get("chunk_size", 4))
    fake.reply(*(reply_from_json(reply) for reply in spec["replies"]))

    events, opened = run_turn(serve(), spec["prompt"])

    assert events[-1]["payload"]["status"] == "answered"
    kept = {"note": spec["note"], "events": events, "messages": opened["messages"]}
    keep(script.stem, normal(kept, folders(work)), "tests/server/test_turns.py")
