"""The fake model server driven from another process, as the desktop's end-to-end tests drive it:
replies queued as JSON over HTTP come back through the real SDK, and the requests can be read."""

import json
import urllib.request

from openai import OpenAI

from fake_model import FakeCall, FakeError, FakeModelServer, FakeReply, reply_from_json


def _post(url: str, body: object) -> dict[str, object]:
    request = urllib.request.Request(  # noqa: S310 - the fake server on 127.0.0.1
        url, data=json.dumps(body).encode(), headers={"content-type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
        return json.loads(response.read())


def test_replies_queued_over_http_answer_the_next_calls() -> None:
    with FakeModelServer(key=None) as server:
        base = f"http://127.0.0.1:{server.port}"
        queued = _post(f"{base}/_fake/reply", [{"text": "Hello from JSON.", "pause": 0.0}])
        client = OpenAI(base_url=server.base_url, api_key="any")
        stream = client.chat.completions.create(
            model="fake-model", messages=[{"role": "user", "content": "hi"}], stream=True
        )
        text = "".join(chunk.choices[0].delta.content or "" for chunk in stream if chunk.choices)
        with urllib.request.urlopen(f"{base}/_fake/requests", timeout=10) as response:  # noqa: S310
            recorded = json.loads(response.read())

    assert queued == {"queued": 1, "left": 1}
    assert text == "Hello from JSON."
    assert recorded["left"] == 0
    assert recorded["requests"][0]["body"]["messages"] == [{"role": "user", "content": "hi"}]


def test_a_reply_in_json_is_a_reply_or_an_error() -> None:
    assert reply_from_json(
        {"text": "x", "calls": [{"name": "read_file", "arguments": "{}"}]}
    ) == FakeReply(text="x", calls=[FakeCall("read_file", "{}")])
    assert reply_from_json({"error": {"status": 429, "message": "slow down"}}) == FakeError(
        429, "slow down"
    )
