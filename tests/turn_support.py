"""A turn kept for the app's tests (step 9c): the task's working folder, the turn run on a real
server, and what the app received made the same on every machine.

The six scripts of `tests/turns/`, the real recordings of `tests/recordings/` and
`scripts/record_turn.py` share it: the same task, the same files, the same cleanup. What is kept
goes to `apps/desktop/src/store/turns/`, where `turns.test.ts` replays it through the screen's
reducer.
"""

import json
import os
from pathlib import Path
from typing import Any

from server_support import AtlasServer

ROOT = Path(__file__).resolve().parents[1]
KEPT = ROOT / "apps" / "desktop" / "src" / "store" / "turns"
#: What stands for the working folder in what is kept and recorded.
WORKSPACE = "<workspace>"
#: Writes the kept files again when set, as `scripts/gen_protocol.py` writes its own.
WRITE = "ATLAS_WRITE_TURNS"

#: The working folder every turn starts in.
FILES = {
    "pytest.log": (
        "FAILED tests/test_price.py::test_total - assert 109.89999999999999 == 109.9\n"
        "1 failed, 41 passed in 1.9s\n"
    ),
    "src/shop/price.py": (
        "from shop.items import Item\n\n\ndef total(items: list[Item]) -> float:\n"
        "    return sum(item.price * item.qty for item in items)\n"
    ),
    "errors.log": (
        "mon ERROR timeout\nmon ERROR timeout\nmon ERROR timeout\n"
        + "tue ERROR invalid_key\n" * 12
        + "wed ERROR timeout\n"
    ),
}

#: The real recordings' task: two or three tools, as the design asks. It names the files, so a
#: model does not spend the recording's calls looking for them (gpt-6-luna did, 15/10/2026).
TASK = (
    "The tests have been failing since yesterday. Read pytest.log and src/shop/price.py, then fix "
    "the function that fails with edit_file. Use your tools; answer in two sentences."
)


def lay_out(work: Path) -> None:
    for name, text in FILES.items():
        path = work / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")


def folders(work: Path) -> tuple[str, ...]:
    """Every spelling of the working folder: as given, resolved, with either slash, and as JSON
    writes it inside a string (backslashes doubled)."""
    plain = {str(work), str(work.resolve()), work.as_posix(), work.resolve().as_posix()}
    escaped = {json.dumps(folder)[1:-1] for folder in plain}
    return tuple(sorted(plain | escaped, key=len, reverse=True))


def run_turn(server: AtlasServer, text: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """One turn of a new conversation: its events, then the conversation opened again."""
    with server.client() as client:
        conversation = client.result("conversation.create")["conversation_id"]
        client.result("conversation.send", {"conversation_id": conversation, "text": text})
        events = client.events_until("message.complete", timeout=600)
        opened = client.result("conversation.open", {"conversation_id": conversation})
    return events, opened


def normal(value: Any, spellings: tuple[str, ...]) -> Any:
    """`value` with the working folder as a marker, the times zero and no cost."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():  # pyright: ignore[reportUnknownVariableType]
            if key == "duration_s":
                out[str(key)] = 0.0  # pyright: ignore[reportUnknownArgumentType]
            elif key == "cost_usd":
                # The fake's price follows the request's size, and the instructions vary with what
                # ran before in the process; the trail never shows it.
                out[str(key)] = None  # pyright: ignore[reportUnknownArgumentType]
            elif key not in ("created_at", "conversation_id"):
                out[str(key)] = normal(item, spellings)  # pyright: ignore[reportUnknownArgumentType]
        return out
    if isinstance(value, list):
        return [normal(item, spellings) for item in value]  # pyright: ignore[reportUnknownVariableType]
    if isinstance(value, str):
        for folder in spellings:
            value = value.replace(folder, WORKSPACE)
        return value.replace("\\", "/") if WORKSPACE in value else value
    return value


def keep(name: str, kept: dict[str, Any], test: str) -> None:
    """Compare what the app received with the kept file; `ATLAS_WRITE_TURNS` writes it.

    The output of a tool that reads the machine (ripgrep's words, a shell's) is kept but not
    compared: CI's ripgrep on Linux is not the one on Windows."""
    text = json.dumps(kept, ensure_ascii=False, indent=1) + "\n"
    out = KEPT / f"{name}.json"
    again = f"{WRITE}=1 uv run pytest {test}"
    if os.environ.get(WRITE):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8", newline="\n")
    assert out.exists(), f"write it: {again}"
    before, now = _steady(json.loads(out.read_text(encoding="utf-8"))), _steady(kept)
    assert before == now, (
        f"What the server says changed ({_first_change(before, now)}); when it is meant, write "
        f"it again: {again}"
    )


def _first_change(before: dict[str, Any], now: dict[str, Any]) -> str:
    for key in ("events", "titles", "messages"):
        for index, (old, new) in enumerate(zip(before[key], now[key], strict=False)):
            if old != new:
                return f"{key}[{index}]: kept {json.dumps(old)[:300]}, now {json.dumps(new)[:300]}"
        if len(before[key]) != len(now[key]):
            return f"{key}: kept {len(before[key])}, now {len(now[key])}"
    return "the note"


#: Tools whose output depends on the machine they run on.
_MACHINE_TOOLS = frozenset({"find_files", "run_command"})


def _steady(kept: dict[str, Any]) -> dict[str, Any]:
    """`kept` with the machine's tools' outputs left out, and the titles apart: a title is written
    in the background as the turn starts, so where it falls among the turn's events varies."""
    calls = {
        event["payload"]["tool_id"]
        for event in kept["events"]
        if event["type"] == "tool.start" and event["payload"]["name"] in _MACHINE_TOOLS
    }
    events = [
        {**event, "payload": {**event["payload"], "result_text": ""}}
        if event["type"] == "tool.complete" and event["payload"]["tool_id"] in calls
        else event
        for event in kept["events"]
        if not (event["type"] == "tool.progress" and event["payload"]["tool_id"] in calls)
    ]
    titles = [event["payload"] for event in events if event["type"] == "title.changed"]
    turn = [event for event in events if event["type"] != "title.changed"]
    messages = [
        {**message, "text": ""} if message.get("call_id") in calls else message
        for message in kept["messages"]
    ]
    return {
        **kept,
        "events": [{**event, "seq": seq} for seq, event in enumerate(turn, start=1)],
        "titles": titles,
        "messages": messages,
    }
