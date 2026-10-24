"""The rules of talking to more than one machine: tailnet peers, the always-on file, the connection
code, `tailscale status`, and the service definitions."""

import base64
import json
import plistlib
import subprocess
import xml.etree.ElementTree as ElementTree

import pytest

from atlas.instances import (
    CODE_PREFIX,
    AlwaysOn,
    CodeError,
    ConnectionCode,
    InstancesError,
    ServiceCommand,
    Transport,
    decode_code,
    encode_code,
    is_tailnet,
    launchd_plist,
    parse_always_on,
    parse_tailscale_status,
    service_label,
    systemd_unit,
    task_xml,
    windows_command_line,
)

TOKEN = "Xk2-token-of-the-home-0123456789abcdef"
COMMAND = ServiceCommand(
    program=r"C:\Atlas V4\.venv\Scripts\pythonw.exe",
    args=("-m", "atlas", "serve", "--always-on", r"C:\Users\someone\.atlas\homes\shop"),
    folder=r"C:\Users\someone\.atlas\homes\shop",
    path=r"C:\tools;C:\Windows",
)
POSIX = ServiceCommand(
    program="/opt/atlas/.venv/bin/python",
    args=("-m", "atlas", "serve", "--always-on", "/home/someone/.atlas/homes/my shop"),
    folder="/home/someone/.atlas/homes/my shop",
    path="/opt/tools:/usr/bin:/bin",
)


@pytest.mark.parametrize(
    "address",
    ["100.64.0.1", "100.101.102.103", "100.127.255.254", "fd7a:115c:a1e0::1", "::ffff:100.64.0.9"],
)
def test_tailscale_addresses_are_the_tailnet(address: str) -> None:
    assert is_tailnet(address)


@pytest.mark.parametrize(
    "address", ["127.0.0.1", "100.63.255.255", "100.128.0.1", "192.168.0.10", "::1", "", "shop"]
)
def test_other_addresses_are_not(address: str) -> None:
    assert not is_tailnet(address)


def test_the_always_on_file_reads_back() -> None:
    assert parse_always_on('{"port": 51234}') == AlwaysOn(51234, open=False)
    assert parse_always_on('{"port": 51234, "open": true}') == AlwaysOn(51234, open=True)


@pytest.mark.parametrize(
    "text", ["", "[]", '{"port": "1"}', '{"port": 0}', '{"port": 70000}', '{"port": 1, "open": 1}']
)
def test_an_always_on_file_atlas_never_wrote_is_refused(text: str) -> None:
    with pytest.raises(InstancesError):
        parse_always_on(text)


def test_a_code_goes_and_comes_back_whole() -> None:
    for code in (
        ConnectionCode("default", 51234, TOKEN),
        ConnectionCode("shop", 18790, TOKEN, tailnet="shop.example-tailnet.ts.net"),
        ConnectionCode("shop", 18790, TOKEN, tailnet="100.101.102.103"),
    ):
        text = encode_code(code)
        assert text.startswith(CODE_PREFIX)
        assert "=" not in text
        assert decode_code(f"  {text}\n") == code


def test_a_code_says_its_transports() -> None:
    assert ConnectionCode("default", 1, TOKEN).transports == (Transport.SSH,)
    assert ConnectionCode("default", 1, TOKEN, "100.64.0.1").transports == (
        Transport.TAILSCALE,
        Transport.SSH,
    )


def test_a_cut_or_foreign_code_is_refused_without_saying_it() -> None:
    whole = encode_code(ConnectionCode("default", 51234, TOKEN))
    for text in (whole[:-12], "hello", whole.replace(CODE_PREFIX, "atlas1x"), CODE_PREFIX + "!!"):
        with pytest.raises(CodeError) as raised:
            decode_code(text)
        assert TOKEN not in str(raised.value)
        assert text not in str(raised.value)


def test_a_code_of_a_newer_atlas_says_to_update() -> None:
    newer = "atlas2." + encode_code(ConnectionCode("default", 1, TOKEN)).removeprefix(CODE_PREFIX)
    with pytest.raises(CodeError, match="newer Atlas"):
        decode_code(newer)


def test_a_code_cannot_bend_the_address() -> None:
    body = json.dumps({"home": "x", "port": 1, "token": TOKEN, "tailnet": "evil.com/path?"})

    text = CODE_PREFIX + base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")
    with pytest.raises(CodeError):
        decode_code(text)


def _status(state: str = "Running", **me: object) -> str:
    fields = {"DNSName": "shop.example-tailnet.ts.net.", "TailscaleIPs": ["100.101.102.103"]}
    fields.update(me)
    return json.dumps({"BackendState": state, "Self": fields})


def test_tailscale_status_gives_the_address_and_name() -> None:
    found = parse_tailscale_status(
        _status(TailscaleIPs=["fd7a:115c:a1e0::5", "100.101.102.103"], DNSName="Shop.Tail.ts.net.")
    )
    assert found.address == "100.101.102.103"
    assert found.name == "shop.tail.ts.net"
    assert parse_tailscale_status(_status(DNSName="")).name is None


@pytest.mark.parametrize(
    ("text", "said"),
    [
        (_status("NeedsLogin"), "not connected"),
        (_status("Stopped"), "not connected"),
        (_status(TailscaleIPs=[]), "no IPv4"),
        ("not json", "cannot read"),
    ],
)
def test_tailscale_status_says_why_the_tailnet_cannot_be_used(text: str, said: str) -> None:
    with pytest.raises(InstancesError, match=said):
        parse_tailscale_status(text)


def test_the_label_names_the_home_and_its_folder() -> None:
    one = service_label("shop", "/home/a/.atlas/homes/shop")
    assert one.startswith("atlas-shop-")
    assert one != service_label("shop", "/home/b/.atlas/homes/shop")
    assert one == service_label("shop", "/home/a/.atlas/homes/shop")


@pytest.mark.parametrize(
    "args",
    [
        ("plain",),
        ("with space", r"C:\Atlas V4\x"),
        ('say "hi"', "trailing\\", r"C:\path with\\"),
        ("",),
    ],
)
def test_windows_arguments_join_as_the_c_runtime_splits_them(args: tuple[str, ...]) -> None:
    assert windows_command_line(args) == subprocess.list2cmdline(args)


def test_the_task_starts_at_logon_without_elevation_and_never_times_out() -> None:
    text = task_xml(r"DOMAIN\someone", COMMAND, "Atlas, home shop", "2026-10-21T22:00:00")
    root = ElementTree.fromstring(text.split("\n", 1)[1])  # noqa: S314 - XML the test made
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}

    def value(path: str) -> str | None:
        found = root.find(path, ns)
        return None if found is None else found.text

    assert value("t:Triggers/t:LogonTrigger/t:UserId") == r"DOMAIN\someone"
    assert value("t:Principals/t:Principal/t:RunLevel") == "LeastPrivilege"
    assert value("t:Settings/t:ExecutionTimeLimit") == "PT0S"
    assert value("t:Settings/t:DisallowStartIfOnBatteries") == "false"
    assert value("t:Settings/t:RestartOnFailure/t:Count") == "999"
    assert value("t:Actions/t:Exec/t:Command") == COMMAND.program
    assert value("t:Actions/t:Exec/t:Arguments") == subprocess.list2cmdline(COMMAND.args)
    assert value("t:Actions/t:Exec/t:WorkingDirectory") == COMMAND.folder


def test_the_task_starts_a_server_that_died_within_a_minute() -> None:
    """RestartOnFailure covers a failed start only: a time trigger from the registration looks
    again every minute, and IgnoreNew drops it while the server runs."""
    text = task_xml(r"DOMAIN\someone", COMMAND, "Atlas, home shop", "2026-10-21T22:00:00")
    root = ElementTree.fromstring(text.split("\n", 1)[1])  # noqa: S314 - XML the test made
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    trigger = root.find("t:Triggers/t:TimeTrigger", ns)
    assert trigger is not None
    assert trigger.findtext("t:StartBoundary", namespaces=ns) == "2026-10-21T22:00:00"
    assert trigger.findtext("t:Repetition/t:Interval", namespaces=ns) == "PT1M"
    assert trigger.find("t:Repetition/t:Duration", ns) is None  # repeats with no end
    assert root.findtext("t:Settings/t:MultipleInstancesPolicy", namespaces=ns) == "IgnoreNew"


def test_the_launchd_agent_runs_at_load_and_is_kept_alive() -> None:
    plist = plistlib.loads(launchd_plist("atlas-shop-1234abcd", POSIX).encode())
    assert plist["Label"] == "atlas-shop-1234abcd"
    assert plist["ProgramArguments"] == [POSIX.program, *POSIX.args]
    assert plist["RunAtLoad"] is True
    assert plist["KeepAlive"] is True
    assert plist["EnvironmentVariables"] == {"PATH": POSIX.path}
    assert plist["WorkingDirectory"] == POSIX.folder


def test_the_systemd_unit_restarts_and_quotes_its_words() -> None:
    unit = systemd_unit(POSIX, "Atlas, home my shop")
    assert "Restart=always\n" in unit
    assert "WantedBy=default.target\n" in unit
    assert (
        'ExecStart=/opt/atlas/.venv/bin/python -m atlas serve --always-on "/home/someone/.atlas'
        '/homes/my shop"\n'
    ) in unit
    assert "Environment=PATH=/opt/tools:/usr/bin:/bin\n" in unit
    spaced = ServiceCommand("/bin/x", (), "/", "/my tools:/bin")
    assert 'Environment="PATH=/my tools:/bin"\n' in systemd_unit(spaced, "x")
    odd = ServiceCommand("/bin/x", ("100%", "$HOME"), "/", "/bin")
    assert "ExecStart=/bin/x 100%% $$HOME\n" in systemd_unit(odd, "x")
