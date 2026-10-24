"""Who may open the WebSocket. Pure.

The loopback guards, checked in this order, each with its own close code:

0. **The protocol** (4426): a client offering another `atlas.v<n>` speaks a protocol this server
   does not (a machine of the user's runs an older or newer Atlas, step 15); the client reads the
   server's number in `/health` and says which side to update.
1. **The server token** (4401). It travels as a WebSocket subprotocol, `atlas.token.<token>` beside
   `atlas.v1`, because a browser (Electron's window included) cannot set headers on a WebSocket,
   and a URL ends up in logs: V3's token sat in the query and reached 583 log lines in clear.
   Compared in constant time.
2. **The Host header, and the Origin** (4403). A page open in the browser can open a WebSocket to
   the local server, since WebSockets skip the browser's same-origin rule; and a site whose name
   resolves to 127.0.0.1 (DNS rebinding) sends its own name as Host. On a loopback address the Host
   must be a loopback name; an `http(s)` Origin must name the same host. Other origins (Electron's
   `file://`, `app://`, `null`) carry no page from the web, and the token guards them.
3. **The peer** (4408). On a loopback address, only a local peer is let in.

On any other address (a container) any peer may come, and the token is what guards it.

**On the tailnet** (a home open to the user's machines, docs/ARCHITECTURE.md, Several machines), a
connection that arrived on the machine's Tailscale address must come from a Tailscale address, and
its Host must be the machine's tailnet address or MagicDNS name; the token and the Origin rule are
the same.
"""

import hmac
import ipaddress
from collections.abc import Set
from urllib.parse import urlsplit

from atlas.instances import is_tailnet
from atlas.server.protocol import PROTOCOL

TOKEN_PREFIX = "atlas.token."  # noqa: S105 - the prefix, not a token
#: A wrong or missing token.
BAD_TOKEN = 4401
#: A Host or Origin that does not name this server.
BAD_ORIGIN = 4403
#: A peer from another machine on a loopback server.
BAD_PEER = 4408
#: Another version of the protocol (HTTP's 426, Upgrade Required).
OTHER_PROTOCOL = 4426

_LOOPBACK_NAMES = frozenset({"localhost", "127.0.0.1", "::1"})


def is_loopback(host: str) -> bool:
    """Whether an address or name is this machine's own."""
    host = host.strip().strip("[]").lower()
    if host in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def refusal(
    *,
    token: str,
    bound_host: str,
    protocols: list[str],
    host: str,
    origin: str | None,
    peer: str | None,
    local: str | None = None,
    tailnet_names: Set[str] = frozenset(),
) -> tuple[int, str] | None:
    """Why a WebSocket upgrade is refused (its close code and a reason for the log), or `None`
    when it may come in. `local` is the address the connection arrived on; `tailnet_names` the
    machine's tailnet address and name while it listens there."""
    presented = [p for p in protocols if p.startswith(TOKEN_PREFIX)]
    versions = [p for p in protocols if p.startswith("atlas.v")]
    if versions and PROTOCOL not in versions:
        return OTHER_PROTOCOL, f"the client speaks {', '.join(versions)}, this server {PROTOCOL}"
    if PROTOCOL not in protocols or len(presented) != 1:
        return BAD_TOKEN, "no token, or not one"
    given = presented[0].removeprefix(TOKEN_PREFIX)
    if not hmac.compare_digest(given.encode(), token.encode()):
        return BAD_TOKEN, "the token does not match"
    host_name = _host_name(host)
    if tailnet_names and local is not None and is_tailnet(local):
        return _tailnet_refusal(host_name, origin, peer, tailnet_names)
    loopback = is_loopback(bound_host)
    if loopback and not is_loopback(host_name):
        return BAD_ORIGIN, f"host {host!r} is not this machine"
    if origin:
        parts = urlsplit(origin)
        if parts.scheme in ("http", "https"):
            named = (parts.hostname or "").lower()
            if (loopback and not is_loopback(named)) or (not loopback and named != host_name):
                return BAD_ORIGIN, f"origin {origin!r} is not this server"
    if loopback and (peer is None or not is_loopback(peer)):
        return BAD_PEER, f"peer {peer!r} is not this machine"
    return None


def _tailnet_refusal(
    host_name: str, origin: str | None, peer: str | None, names: Set[str]
) -> tuple[int, str] | None:
    if host_name not in {name.lower() for name in names}:
        return BAD_ORIGIN, f"host {host_name!r} is not this machine on the tailnet"
    if origin:
        # The app's own window, served from its machine's loopback while developing, may come;
        # a page from the web may not.
        parts = urlsplit(origin)
        named = (parts.hostname or "").lower()
        if parts.scheme in ("http", "https") and named != host_name and not is_loopback(named):
            return BAD_ORIGIN, f"origin {origin!r} is not this server"
    if peer is None or not is_tailnet(peer):
        return BAD_PEER, f"peer {peer!r} is not on the tailnet"
    return None


def _host_name(host: str) -> str:
    """The name in a Host header, without its port."""
    host = host.strip().lower()
    if host.startswith("["):
        return host[1 : host.find("]")] if "]" in host else host
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host
