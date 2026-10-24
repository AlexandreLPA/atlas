"""Monitors (step 16k): Claude Code's `monitors/monitors.json` read and consented to, and a real
command run in the background, its lines gathered into notices, stopped with its conversation.

The monitor's command is a Python script run by this interpreter through the shell a plugin's
command takes (bash; Git Bash on Windows, PowerShell without it), so it runs the same everywhere.
"""

import asyncio
import json
import sys
from pathlib import Path

import pytest

from atlas.hooks import ConversationMonitors, PluginMonitor
from atlas.plugins import ManifestError
from atlas.plugins.model import parse_manifest

TICKS = """
import sys, time
for n in range(int(sys.argv[1])):
    print(f"tick {n}", flush=True)
    time.sleep(float(sys.argv[2]))
"""


def _monitor(tmp_path: Path, count: int, pause: float, *, when: str = "always") -> PluginMonitor:
    root = tmp_path / "plugin"
    root.mkdir(exist_ok=True)
    (root / "ticks.py").write_text(TICKS, encoding="utf-8")
    python = Path(sys.executable).as_posix()
    command = f'"{python}" "${{CLAUDE_PLUGIN_ROOT}}/ticks.py" {count} {pause}'
    return PluginMonitor("deploy", "ticks", command, "Deploy status", when, root, tmp_path / "data")


def test_monitors_are_read_as_claude_code_writes_them() -> None:
    entry = {"name": "status", "command": "./watch.sh", "description": "Deploy  status"}
    later = {**entry, "name": "renders", "when": "on-skill-invoke:render"}
    manifest = parse_manifest(
        json.dumps({"name": "video"}),
        {"monitors"},
        folder_name="video",
        monitors_text=json.dumps([entry, later]),
    )
    assert [(m.name, m.description, m.when) for m in manifest.monitors] == [
        ("status", "Deploy status", "always"),
        ("renders", "Deploy status", "on-skill-invoke:render"),
    ]
    assert manifest.unsupported == ()
    assert manifest.declaration() == (
        "Runs `./watch.sh` in the background of your conversations (from their first turn): "
        "Deploy status. Each line it prints reaches the model, which answers it.",
        "Runs `./watch.sh` in the background of your conversations (once the skill render is "
        "used): Deploy status. Each line it prints reaches the model, which answers it.",
    )
    # The manifest's `experimental.monitors` takes the file's place; its themes Atlas leaves.
    inline = parse_manifest(
        json.dumps({"name": "video", "experimental": {"monitors": [entry], "themes": "./t"}}),
        set(),
        folder_name="video",
        monitors_text=json.dumps([later]),
    )
    assert [m.name for m in inline.monitors] == ["status"]
    assert inline.unsupported == ("themes",)
    changed = parse_manifest(
        json.dumps({"name": "video", "experimental": {"monitors": [{**entry, "command": "x"}]}}),
        set(),
        folder_name="video",
    )
    assert changed.fingerprint() != inline.fingerprint(), "another command asks again"


@pytest.mark.parametrize(
    ("monitors", "problem"),
    [
        ([{"name": "a", "command": "x"}], "needs a name, a command and a description"),
        ([{"name": "a", "command": "x", "description": "d", "every": 5}], "every"),
        ([{"name": "a", "command": "x", "description": "d", "when": "daily"}], "always or"),
        ([{"name": "a", "command": "x ${user_config.k}", "description": "d"}], "takes none"),
        ([{"name": "a", "command": "x", "description": "d"}] * 2, "same name"),
        ("./monitors.json", "names a file"),
        ({"a": 1}, "an array"),
    ],
)
def test_a_monitor_claude_code_refuses_is_refused(monitors: object, problem: str) -> None:
    with pytest.raises(ManifestError, match=problem):
        parse_manifest(
            json.dumps({"name": "video", "experimental": {"monitors": monitors}}),
            set(),
            folder_name="video",
        )


def test_a_monitor_s_lines_reach_the_conversation_gathered_and_it_ends_once(
    tmp_path: Path,
) -> None:
    notices: list[str] = []
    monitors = ConversationMonitors(notices.append, lambda: tmp_path, gather=0.5)
    monitor = _monitor(tmp_path, 3, 0.05)

    async def scenario() -> None:
        monitors.start([monitor])
        monitors.start([monitor])  # its time came again: it runs once
        for _ in range(200):
            if any("ended" in notice for notice in notices):
                break
            await asyncio.sleep(0.05)
        await monitors.stop()

    asyncio.run(scenario())
    assert notices == [
        "[Monitor deploy:ticks: Deploy status]\ntick 0\ntick 1\ntick 2",
        "[Monitor deploy:ticks ended (exit code 0).]",
    ]


def test_a_monitor_that_waits_for_a_skill_starts_with_it_and_stops_with_the_conversation(
    tmp_path: Path,
) -> None:
    notices: list[str] = []
    monitors = ConversationMonitors(notices.append, lambda: tmp_path, gather=0.05)
    monitor = _monitor(tmp_path, 1000, 0.05, when="render")

    async def scenario() -> list[str]:
        monitors.start([monitor])
        monitors.start([monitor], skill="other")
        await asyncio.sleep(0.3)
        assert monitors.running == [], "not before its skill"
        monitors.start([monitor], skill="deploy:render")
        for _ in range(200):
            if notices:
                break
            await asyncio.sleep(0.05)
        running = monitors.running
        await monitors.stop()
        return running

    assert asyncio.run(scenario()) == ["deploy:ticks"]
    assert notices[0].startswith("[Monitor deploy:ticks: Deploy status]\ntick 0")
    assert not any("ended" in notice for notice in notices), "stopped, not ended by itself"
