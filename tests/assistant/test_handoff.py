"""The handoff packet a session's route gives a new session of another executor (asked for by the
user on 21/10/2026): put together from what was kept, the same every time, with no model call."""

import json

from atlas.assistant import HANDOFF_MARK, Handoff, entries_of, handoff_packet
from atlas.models import (
    AssistantMessage,
    Attachment,
    AttachmentKind,
    ToolCall,
    ToolResult,
    ToolStatus,
    UserMessage,
)
from atlas.plugins import TranscriptEntry


def _handoff(*entries: TranscriptEntry, changed: tuple[str, ...] = ()) -> Handoff:
    return Handoff(
        source="Claude Code · main account",
        target="Codex · main account",
        folder="/work/site",
        reason="Claude Code · main account reached its plan's limit.",
        entries=entries,
        session="s-1",
        changed=changed,
    )


def _sections(packet: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for part in packet.split("\n## ")[1:]:
        title, _, body = part.partition("\n")
        found[title] = body
    return found


def test_the_packet_carries_the_task_the_switch_the_folder_the_work_and_what_is_pending() -> None:
    plan = {
        "todos": [
            {"content": "Fix the header", "status": "completed"},
            {"content": "Fix the footer", "status": "in_progress"},
            {"content": "Run the tests", "status": "pending"},
        ]
    }
    handoff = _handoff(
        TranscriptEntry("text", "Fix the header and the footer.", by="user", files=("/a/s.png",)),
        TranscriptEntry("tool", json.dumps(plan), tool="TodoWrite", call="c1"),
        TranscriptEntry("result", "ok", call="c1"),
        TranscriptEntry("tool", '{"file_path": "index.html"}', tool="Edit", call="c2"),
        TranscriptEntry("result", "permission denied", call="c2", failed=True),
        TranscriptEntry("question", "Which footer colour?", tool="AskUserQuestion", call="q1"),
        TranscriptEntry("text", "Also keep the logo.", by="user"),
        changed=("index.html", "index.html", "style.css"),
    )

    packet = handoff_packet(handoff)
    sections = _sections(packet)

    assert packet == handoff_packet(handoff), "the same handoff, the same packet"
    assert packet.startswith(HANDOFF_MARK)
    assert "Claude Code · main account" in packet.split("\n")[0]
    assert sections["Why you are taking over"] == handoff.reason + "\n"
    assert sections["Working folder"] == "/work/site\n"
    assert sections["The task"] == "Fix the header and the footer.\n"
    assert sections["The latest instruction"] == "Also keep the logo.\n"
    assert sections["Pending work and open questions"].splitlines() == [
        "- [ ] Fix the footer (in_progress)",
        "- [ ] Run the tests (pending)",
        "- Asked the user, with no answer yet: Which footer colour?",
        "- The last instruction above was stopped by the limit before the session answered it.",
    ]
    assert sections["Files"].splitlines() == [
        "- changed: index.html",
        "- changed: style.css",
        "- attached by the user: /a/s.png",
    ]
    steps = sections["What happened, oldest first"]
    assert '- Tool call Edit: {"file_path": "index.html"}' in steps
    assert "- Tool result (failed): permission denied" in steps
    assert "Codex · main account" in sections["Continue"]
    assert "s-1" in sections["Continue"]


def test_the_newest_steps_are_kept_when_the_packet_would_pass_its_size() -> None:
    entries = [TranscriptEntry("text", "Build the site.", by="user")]
    entries += [TranscriptEntry("text", f"Step {n}: " + "x" * 900) for n in range(60)]

    packet = handoff_packet(_handoff(*entries), limit=12_000)
    steps = _sections(packet)["What happened, oldest first"]

    assert len(packet) <= 12_000
    assert "Step 59:" in steps, "the newest kept"
    assert "Step 0:" not in steps
    assert steps.startswith("(")
    assert "earlier steps left out" in steps.splitlines()[0]
    assert _sections(packet)["The task"] == "Build the site.\n", "the task always kept"


def test_a_long_step_keeps_its_start_and_its_end() -> None:
    result = "start " + "y" * 5_000 + " end"

    packet = handoff_packet(
        _handoff(TranscriptEntry("text", "Run it.", by="user"), TranscriptEntry("result", result))
    )

    assert "- Tool result: start" in packet
    assert " end\n" in packet
    assert "characters left out" in packet
    assert "y" * 1_000 not in packet


def test_a_session_that_began_from_a_packet_hands_on_the_first_task() -> None:
    first = handoff_packet(_handoff(TranscriptEntry("text", "Ship the release.", by="user")))

    second = handoff_packet(
        _handoff(
            TranscriptEntry("text", first, by="user"),
            TranscriptEntry("text", "Tagged v1.2; the notes are left."),
        )
    )
    sections = _sections(second)

    assert sections["The task"] == "Ship the release.\n"
    assert "The latest instruction" not in sections
    assert "- User: (the handoff packet from the session before)" in second
    assert second.count(HANDOFF_MARK) == 1, "the packet before is not pasted in again"
    assert "Pending work and open questions" not in sections, "the agent spoke last"


def test_codex_s_plan_and_an_unreadable_one_are_read_as_far_as_they_can_be() -> None:
    plan = {"plan": [{"step": "Write the notes", "status": "pending"}, {"step": "Tag"}]}
    read = handoff_packet(
        _handoff(
            TranscriptEntry("text", "Release.", by="user"),
            TranscriptEntry("tool", json.dumps(plan), tool="update_plan"),
            TranscriptEntry("text", "Working."),
        )
    )
    cut = handoff_packet(
        _handoff(
            TranscriptEntry("text", "Release.", by="user"),
            TranscriptEntry("tool", '{"todos": [', tool="TodoWrite"),
            TranscriptEntry("text", "Working."),
        )
    )

    assert _sections(read)["Pending work and open questions"].splitlines() == [
        "- [ ] Write the notes (pending)",
        "- [ ] Tag",
    ]
    assert "Pending work and open questions" not in _sections(cut)


def test_atlas_s_own_record_of_a_card_reads_as_the_harness_s_steps() -> None:
    shot = Attachment("at-1", "shot.png", "image/png", 10, AttachmentKind.IMAGE)
    history = [
        UserMessage("Fix it.", (shot,)),
        AssistantMessage("Looking.", (ToolCall("c1", "Read", '{"path": "a.py"}'),)),
        ToolResult("c1", "boom", status=ToolStatus.FAILED),
        AssistantMessage(reasoning="thinking only"),
    ]

    entries = entries_of(history, {"at-1": "/home/attachments/at-1.png"})

    assert entries == (
        TranscriptEntry("text", "Fix it.", by="user", files=("/home/attachments/at-1.png",)),
        TranscriptEntry("text", "Looking."),
        TranscriptEntry("tool", '{"path": "a.py"}', tool="Read", call="c1"),
        TranscriptEntry("result", "boom", call="c1", failed=True),
    )
