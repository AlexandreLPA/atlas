"""The browser's rules: the tool's words, what a call may reach, and how Chrome and the browser
engine are started. Pure.

Decided with the user on 12/10/2026 (docs/ARCHITECTURE.md, Browser):

- **One code tool**: the model writes Python against the helpers of Browser Use's browser-harness,
  which run it against the browser; the browser and a workspace last across calls.
- **Atlas's own Chrome profile**, and two throwaway modes, clean and headless.
- **No takeover**: a lease, the user taking the window over from the agent, was dropped on
  20/10/2026 with the screen that offered it (decided with the user). At a login the model stops
  and asks; the user signs in in the window and says so.
- **The cloud metadata floor**: an address that hands out a cloud machine's credentials is never
  reached, whatever the browser.
"""

import ipaddress
import re
from collections.abc import Mapping
from enum import StrEnum
from hashlib import sha256
from urllib.parse import urlsplit

#: The tool's name.
TOOL_NAME = "browser_exec"
#: Seconds a call may run when the model does not say, and the bounds.
DEFAULT_TIMEOUT = 300
MIN_TIMEOUT = 5
MAX_TIMEOUT = 1800
#: The end of the engine's error output kept in a result.
STDERR_CHARS = 4_000
#: The file Chrome writes in its profile with the port and path of its DevTools endpoint.
DEVTOOLS_FILE = "DevToolsActivePort"


class BrowserMode(StrEnum):
    """Which browser a call drives."""

    #: Atlas's own profile, with the sites the user signed into, in a window.
    PROFILE = "profile"
    #: A temporary profile, signed into nothing, in a window.
    CLEAN = "clean"
    #: A temporary profile with no window.
    HEADLESS = "headless"

    @property
    def windowed(self) -> bool:
        return self is not BrowserMode.HEADLESS

    @property
    def kept(self) -> bool:
        """Whether its profile outlives the browser."""
        return self is BrowserMode.PROFILE


# -- the tool's words ----------------------------------------------------------------------------

#: What the tool does, naming the browser Atlas opens.
_HEADER = (
    "Controls a real web browser. `code` runs as complete Python, standard library included, with "
    "the browser helpers already imported, and what it prints to stdout is the result. Open "
    "`code` with a one-line comment telling the user in plain words what this step does, 60 "
    "chars at most (e.g. `# Searching Amazon for paper towels`).\n\n"
    "STATE: the browser session and the workspace stay from one call to the next; Python "
    "variables do NOT, since every call starts a new interpreter. The workspace folder is "
    "$BH_AGENT_WORKSPACE, and each call imports by itself any function defined in the "
    "agent_helpers.py kept there. When a task spans many items ('all N products / every entry'), "
    "add each batch to a JSON or CSV file in the workspace, then load it back and combine it in "
    "code: drop duplicates, count and sort in Python, never in your head, and before answering "
    "check the number collected against the number asked for.\n\n"
    "Put a whole sub-procedure (navigate, wait, extract, act) in one call rather than a call for "
    "each action. A long extraction, though, goes better as several medium calls that add to "
    "workspace files than as one huge call, so that a timeout loses nothing done so far."
)
#: For a model that sees images.
_VISION = (
    " Screenshots are attached to your context automatically: when the exec output holds a path "
    "from capture_screenshot(), that image comes back with this tool's result, and you look at "
    "it yourself with your own vision."
)
#: For a model that does not see images.
_TEXT_ONLY = (
    " Your model cannot view images, so lean on text: page_info() to learn the state, js() to "
    "read and pull text out of the DOM, fill_input(selector, text) to fill inputs, and "
    "js(\"document.querySelector('…').click()\") to click. Leave aside the workflow built on "
    "screenshots that is described below."
)
#: The engine's helpers.
_HELPERS = (
    "\n\nHELPERS (pre-imported): new_tab(url) opens a page or navigates (use it for the FIRST "
    "navigation); goto_url(url) navigates in the current tab; wait_for_load() comes after a "
    "navigation; page_info() sums up the state of the current page; js(expr) runs a JS "
    "expression and gives back its value (js('document.title'); a function body goes in "
    "js('(() => {...})()'), since a bare '() => {...}' comes back as the function, never called); "
    "fill_input(selector, text) types into an input; click_at_xy(x, y) clicks at viewport "
    "coordinates; capture_screenshot() saves a screenshot and returns its path (print it); "
    "cdp('Domain.method', **kwargs) is raw CDP: cdp('Accessibility.getFullAXTree')['nodes'] "
    "gives every element's role/name/backendDOMNodeId (thousands of nodes, so filter them in "
    "Python before printing), and cdp('DOM.getBoxModel', backendNodeId=n) then gives the "
    "coordinates to click. ensure_real_tab() gets back from a stale or internal tab."
)
#: Atlas's modes, and the stop at logins, CAPTCHAs and payments (decided with the user).
_MODES = (
    "\n\nMODES: 'profile' (default) is Atlas's own browser profile, where the user signed into "
    "their sites: use it to act as them. 'clean' is a fresh profile signed into nothing; "
    "'headless' is a fresh profile with no window, for reading and extracting.\n\n"
    "THE USER'S PART: never guess or type credentials. At a login, a CAPTCHA, a payment or "
    "anything only the user should do, stop and ask them to do it in the browser window; once they "
    "say it is done, read the page again before acting."
)

PARAMETERS: dict[str, object] = {
    "type": "object",
    "properties": {
        "code": {
            "type": "string",
            "description": (
                "The Python to run, with the browser helpers already imported. Whatever you "
                "need back, print(...) it."
            ),
        },
        "mode": {
            "type": "string",
            "enum": [mode.value for mode in BrowserMode],
            "description": "Which browser: 'profile' (default), 'clean' or 'headless'.",
        },
        "timeout_s": {
            "type": "integer",
            "description": (
                f"The longest wait, in seconds, for the code to end ({DEFAULT_TIMEOUT} by "
                f"default, {MAX_TIMEOUT} at most)."
            ),
        },
    },
    "required": ["code"],
}


def description(sees_images: bool) -> str:
    """The tool's description, for a model that sees images or one that does not."""
    return _HEADER + (_VISION if sees_images else _TEXT_ONLY) + _HELPERS + _MODES


def timeout_of(value: object) -> int:
    """The seconds a call may run: the model's within the bounds, the default otherwise."""
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return DEFAULT_TIMEOUT
    try:
        return max(MIN_TIMEOUT, min(int(value), MAX_TIMEOUT))
    except ValueError:
        return DEFAULT_TIMEOUT


# -- what a call may reach -------------------------------------------------------------------------

#: The http and https addresses written in the code.
_URL = re.compile(r"https?://[^\s'\"\\)]+", re.IGNORECASE)
#: Cloud metadata names.
_METADATA_HOSTS = frozenset({"metadata.google.internal", "metadata.goog"})
#: Cloud metadata and credential addresses.
_METADATA_V4 = ("169.254.169.254", "169.254.170.2", "169.254.169.253", "100.100.100.200")
#: Those, the same written as IPv4-mapped IPv6, and the IPv6 one.
_METADATA_IPS = frozenset(
    map(
        ipaddress.ip_address,
        [*_METADATA_V4, *[f"::ffff:{v4}" for v4 in _METADATA_V4], "fd00:ec2::254"],
    )
)
#: The whole link-local range, which no site lives in.
_LINK_LOCAL = tuple(
    ipaddress.ip_network(network) for network in ("169.254.0.0/16", "::ffff:169.254.0.0/112")
)
#: What the model reads when a call would reach a cloud metadata address.
METADATA_REFUSED = "Blocked: URL targets a cloud metadata endpoint"
#: What it reads when an address carries a secret Atlas knows.
VAULT_URL_REFUSED = (
    "Blocked: the URL carries a secret from Atlas's vault. Never put credentials in a URL."
)


def urls_in(code: str) -> list[str]:
    return _URL.findall(code)


def host_of(url: str) -> str:
    """The URL's host, lowercased and without a trailing dot; empty when it has none."""
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return ""
    return host.rstrip(".").lower()


def metadata_address(address: str) -> bool:
    """Whether an IP address is a cloud metadata one, or in the link-local range."""
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    return ip in _METADATA_IPS or any(ip in network for network in _LINK_LOCAL)


def metadata_host(host: str) -> bool:
    """Whether a host is a cloud metadata name or address, read as written (the service also
    resolves names)."""
    return host in _METADATA_HOSTS or metadata_address(host)


# -- Chrome and the engine -------------------------------------------------------------------------


def chrome_arguments(program: str, profile: str, *, headless: bool) -> list[str]:
    """Chrome's command line: its own profile folder, which Chrome 136 and later require for
    remote debugging, and a DevTools port of the system's choosing, written in the profile."""
    return [
        program,
        f"--user-data-dir={profile}",
        "--remote-debugging-port=0",
        "--no-first-run",
        "--no-default-browser-check",
        *(["--headless=new"] if headless else []),
        "about:blank",
    ]


def devtools_endpoint(text: str) -> str | None:
    """The browser's DevTools WebSocket from Chrome's `DevToolsActivePort` file: its port, then
    its path."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 2 or not lines[0].isdigit() or not lines[1].startswith("/"):
        return None
    return f"ws://127.0.0.1:{lines[0]}{lines[1]}"


def session_name(conversation_id: str, mode: BrowserMode) -> str:
    """The engine's session for a conversation in a mode: its own daemon and tab (`BU_NAME`,
    1 to 64 letters, digits, `-` and `_`)."""
    return f"atlas-{mode.value}-{sha256(conversation_id.encode()).hexdigest()[:16]}"


#: The engine's variables Atlas sets or removes: no cloud browser, no telemetry (it sends the code
#: it ran to PostHog), no update check (it tells the agent to upgrade itself).
_ENGINE_DROPPED = (
    "BU_CDP_URL",
    "BU_AUTOSPAWN",
    "BROWSER_USE_API_KEY",
    "BH_RUNTIME_DIR",
    "BH_TMP_DIR",
    "BH_CONFIG_DIR",
    "BH_DOMAIN_SKILLS",
    "BH_REQUIRE_EXISTING_DAEMON",
    "PYTHONHOME",
    "PYTHONPATH",
)


def engine_environment(
    environ: Mapping[str, str],
    *,
    home: str,
    workspace: str,
    session: str,
    endpoint: str,
    runtime: str | None = None,
) -> dict[str, str]:
    """The engine's environment: Atlas's, its browser's endpoint, its session, and its files in
    the home; and, where it listens on a Unix socket, a short folder for that socket."""
    kept = {name: value for name, value in environ.items() if name not in _ENGINE_DROPPED}
    if runtime is not None:
        kept["BH_RUNTIME_DIR"] = runtime
    kept.update(
        {
            "BU_CDP_WS": endpoint,
            "BU_NAME": session,
            "BH_HOME": home,
            "BH_AGENT_WORKSPACE": workspace,
            "BH_TELEMETRY": "0",
            "BROWSER_HARNESS_TELEMETRY": "0",
            "ANONYMIZED_TELEMETRY": "false",
            "BH_UPDATE_CHECK": "0",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUNBUFFERED": "1",
        }
    )
    return kept


#: Run before the code: the engine's daemon attaches to the first page it finds, so two
#: conversations on one browser would land on the same tab; each is steered once onto a tab of its
#: own, the marker keyed by the session and the daemon's pid.
OWN_TAB_PREAMBLE = """\
# atlas: a tab for this session alone, opened once in each daemon's life
def _atlas_ensure_own_tab():
    import os, tempfile
    session = os.environ.get("BU_NAME", "default")
    daemon = "0"
    try:
        from browser_harness import _ipc
        daemon = _ipc.pid_path(session).read_text().strip() or "0"
    except Exception:
        pass
    user = os.getuid() if hasattr(os, "getuid") else 0
    done = os.path.join(tempfile.gettempdir(), f"atlas-bu-owntab-{user}-{session}-{daemon}")
    if not os.path.exists(done):
        try:
            tab = cdp("Target.createTarget", url="about:blank").get("targetId")
            if tab:
                switch_tab(tab)
        except Exception:
            pass
        try:
            open(done, "w").close()
        except OSError:
            pass
_atlas_ensure_own_tab()
del _atlas_ensure_own_tab
"""

#: Screenshot paths the code printed.
_IMAGE_PATH = re.compile(r"((?:[A-Za-z]:[\\/]|/)[^\s\"']+?\.(?:png|jpe?g|webp))", re.IGNORECASE)


def printed_images(output: str) -> list[str]:
    """The image paths the code printed, last first."""
    return list(reversed(_IMAGE_PATH.findall(output)))


def timed_out(seconds: int) -> str:
    """What the model reads for a call that ran out of time."""
    return (
        f"browser_exec timed out after {seconds}s. Try again with a larger timeout_s (at most "
        f"{MAX_TIMEOUT}), or break the work into several calls that add to workspace files: what "
        "is already written in the workspace is kept."
    )
