"""Event numbers and the replay a client that comes back asks for."""

from typing import Any

from atlas.server import Replay


def _event(text: str) -> dict[str, Any]:
    return {"type": "message.delta", "payload": {"text": text}}


def test_events_are_numbered_per_conversation_and_replayed_after_a_number() -> None:
    replay = Replay()
    first = _event("a")

    assert replay.record("c1", first) == 1
    assert first["seq"] == 1
    assert replay.record("c1", _event("b")) == 2
    assert replay.record("c2", _event("x")) == 1

    events, truncated = replay.since("c1", 1)
    assert events == [{"type": "message.delta", "payload": {"text": "b"}, "seq": 2}]
    assert truncated is False
    assert replay.latest("c1") == 2
    assert replay.latest("nobody") == 0


def test_a_recorded_event_cannot_be_changed_afterwards() -> None:
    replay = Replay()
    event = _event("original")
    replay.record("c1", event)
    event["payload"] = {"text": "changed"}

    assert replay.since("c1", 0)[0][0]["payload"] == {"text": "original"}


def test_dropped_events_mark_the_replay_as_truncated() -> None:
    replay = Replay(events=3)
    for n in range(5):
        replay.record("c1", _event(str(n)))

    events, truncated = replay.since("c1", 1)
    assert [e["seq"] for e in events] == [3, 4, 5]
    assert truncated is True
    assert replay.since("c1", 2)[1] is False


def test_an_oversized_event_is_not_kept_and_leaves_its_mark() -> None:
    replay = Replay(per_conversation=50)
    replay.record("c1", _event("x" * 100))

    assert replay.since("c1", 0) == ([], True)


def test_a_dropped_conversation_keeps_its_numbering() -> None:
    replay = Replay(conversations=1)
    replay.record("c1", _event("a"))
    replay.record("c2", _event("b"))

    assert replay.record("c1", _event("c")) == 2
    events, truncated = replay.since("c1", 0)
    assert [e["seq"] for e in events] == [2]
    assert truncated is True


def test_the_bytes_of_every_conversation_together_are_bounded() -> None:
    replay = Replay(in_all=120)
    for n in range(4):
        replay.record(f"c{n}", _event("y" * 20))

    assert replay.since("c0", 0) == ([], True)
    assert [e["seq"] for e in replay.since("c3", 0)[0]] == [1]


def test_each_process_has_its_own_epoch() -> None:
    assert Replay().epoch != Replay().epoch
