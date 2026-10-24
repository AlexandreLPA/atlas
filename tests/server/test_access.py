"""Who may open the WebSocket: the loopback guards."""

from typing import Any

import pytest

from atlas.server import (
    BAD_ORIGIN,
    BAD_PEER,
    BAD_TOKEN,
    OTHER_PROTOCOL,
    PROTOCOL,
    TOKEN_PREFIX,
    refusal,
)

TOKEN = "s3cret-token"
GOOD = [PROTOCOL, TOKEN_PREFIX + TOKEN]
#: Listening on every interface, as inside a container.
EVERYWHERE = "0.0.0.0"  # noqa: S104


def _refusal(**given: Any) -> tuple[int, str] | None:
    asked: dict[str, Any] = {
        "token": TOKEN,
        "bound_host": "127.0.0.1",
        "protocols": GOOD,
        "host": "127.0.0.1:18790",
        "origin": None,
        "peer": "127.0.0.1",
    }
    asked.update(given)
    return refusal(**asked)


def test_a_local_client_with_the_token_comes_in() -> None:
    assert _refusal() is None
    assert _refusal(host="localhost:18790", peer="::1") is None
    assert _refusal(host="[::1]:18790") is None


@pytest.mark.parametrize(
    "protocols",
    [
        [],
        [PROTOCOL],
        [TOKEN_PREFIX + TOKEN],
        [PROTOCOL, TOKEN_PREFIX + "wrong"],
        [PROTOCOL, TOKEN_PREFIX + TOKEN, TOKEN_PREFIX + TOKEN],
    ],
)
def test_without_exactly_one_right_token_the_answer_is_4401(protocols: list[str]) -> None:
    refused = _refusal(protocols=protocols)

    assert refused is not None
    assert refused[0] == BAD_TOKEN


@pytest.mark.parametrize(
    ("host", "origin"),
    [
        ("evil.example:18790", None),  # DNS rebinding: a site's name resolving to 127.0.0.1
        ("127.0.0.1:18790", "http://evil.example"),
        ("127.0.0.1:18790", "https://evil.example:443"),
    ],
)
def test_a_page_from_the_web_is_refused_with_4403(host: str, origin: str | None) -> None:
    refused = _refusal(host=host, origin=origin)

    assert refused is not None
    assert refused[0] == BAD_ORIGIN


@pytest.mark.parametrize("origin", ["file://", "null", "app://atlas", "http://localhost:5173"])
def test_the_desktop_and_local_pages_come_in(origin: str) -> None:
    assert _refusal(origin=origin) is None


def test_a_peer_from_another_machine_is_refused_on_a_loopback_server() -> None:
    refused = _refusal(peer="192.168.0.20")

    assert refused is not None
    assert refused[0] == BAD_PEER
    assert _refusal(peer=None) is not None


def test_in_a_container_any_peer_with_the_token_comes_in() -> None:
    assert _refusal(bound_host=EVERYWHERE, host="atlas.lan:18790", peer="10.0.0.5") is None
    refused = _refusal(bound_host=EVERYWHERE, host="atlas.lan", origin="http://evil.example")
    assert refused is not None
    assert refused[0] == BAD_ORIGIN
    assert _refusal(bound_host=EVERYWHERE, host="atlas.lan", origin="http://atlas.lan:80") is None


#: This machine on its tailnet, as an open home's server listens there.
TAILNET_ADDRESS = "100.101.102.103"
TAILNET_NAMES = frozenset({TAILNET_ADDRESS, "shop.example-tailnet.ts.net"})


def _on_tailnet(**given: Any) -> tuple[int, str] | None:
    asked: dict[str, Any] = {
        "host": f"{TAILNET_ADDRESS}:18791",
        "peer": "100.64.0.7",
        "local": TAILNET_ADDRESS,
        "tailnet_names": TAILNET_NAMES,
    }
    asked.update(given)
    return _refusal(**asked)


def test_a_machine_of_the_tailnet_with_the_token_comes_in() -> None:
    assert _on_tailnet() is None
    assert _on_tailnet(host="shop.example-tailnet.ts.net:18791") is None
    assert _on_tailnet(peer="fd7a:115c:a1e0::1") is None


def test_on_the_tailnet_a_peer_from_elsewhere_is_refused_with_4408() -> None:
    refused = _on_tailnet(peer="203.0.113.9")
    assert refused is not None
    assert refused[0] == BAD_PEER


def test_on_the_tailnet_the_token_is_still_asked() -> None:
    refused = _on_tailnet(protocols=[PROTOCOL, TOKEN_PREFIX + "wrong"])
    assert refused is not None
    assert refused[0] == BAD_TOKEN


@pytest.mark.parametrize(
    ("host", "origin"),
    [
        ("attacker.example:18791", None),
        ("127.0.0.1:18791", None),
        (f"{TAILNET_ADDRESS}:18791", "https://attacker.example"),
    ],
)
def test_on_the_tailnet_another_host_or_origin_is_refused_with_4403(
    host: str, origin: str | None
) -> None:
    refused = _on_tailnet(host=host, origin=origin)
    assert refused is not None
    assert refused[0] == BAD_ORIGIN


def test_a_tailnet_address_means_nothing_while_the_home_is_not_open() -> None:
    """With no tailnet names (not listening there), the loopback rules decide as before."""
    refused = _on_tailnet(tailnet_names=frozenset())
    assert refused is not None
    assert refused[0] == BAD_ORIGIN


def test_the_loopback_socket_of_an_open_home_keeps_its_rules() -> None:
    assert _on_tailnet(host="127.0.0.1:18791", peer="127.0.0.1", local="127.0.0.1") is None
    refused = _on_tailnet(host="127.0.0.1:18791", peer="100.64.0.7", local="127.0.0.1")
    assert refused is not None
    assert refused[0] == BAD_PEER


def test_another_version_of_the_protocol_is_refused_with_4426() -> None:
    refused = _refusal(protocols=["atlas.v1", TOKEN_PREFIX + TOKEN])
    assert refused is not None
    assert refused[0] == OTHER_PROTOCOL
    assert "atlas.v1" in refused[1]
