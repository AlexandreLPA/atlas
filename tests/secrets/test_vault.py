"""The vault: what goes in comes back, damage stays local, and the key is found or refused clearly.

Ported from V3's test_secret_vault.py and test_m24_cofre_portatil.py. Every test runs real age
encryption on files in a temporary home, and on Windows the real DPAPI: nothing stands in for the
piece under proof.
"""

import base64
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pyrage
import pytest

from atlas.home import (
    REDACTED,
    Home,
    Redactor,
    SecretText,
    VaultSettings,
    is_private,
    open_home,
    parse_utc,
)
from atlas.secrets import (
    KEY_FILE_VARIABLE,
    PLAIN_KEY_FILE,
    PROTECTED_KEY_FILE,
    InvalidSecretNameError,
    SecretsError,
    SecretUnreadableError,
    Vault,
    VaultDamagedError,
    VaultLockedError,
)

HOME_KEY_FILE = PROTECTED_KEY_FILE if sys.platform == "win32" else PLAIN_KEY_FILE


@pytest.fixture
def home(tmp_path: Path) -> Home:
    return open_home(tmp_path / "home")


def _vault(
    home: Home, settings: VaultSettings | None = None, redactor: Redactor | None = None
) -> Vault:
    return Vault(home.secrets_folder, settings or VaultSettings(), redactor or Redactor())


def _new_key() -> tuple[str, str]:
    identity = pyrage.x25519.Identity.generate()
    return str(identity), str(identity.to_public())


def _document(vault: Vault) -> dict[str, dict[str, dict[str, str]]]:
    return json.loads(vault.path.read_text(encoding="utf-8"))


def _damage(vault: Vault, name: str) -> None:
    document = json.loads(vault.path.read_text(encoding="utf-8"))
    blob = bytearray(base64.b64decode(document["entries"][name]["ciphertext"]))
    blob[len(blob) // 2] ^= 0xFF
    document["entries"][name]["ciphertext"] = base64.b64encode(bytes(blob)).decode("ascii")
    vault.path.write_text(json.dumps(document), encoding="utf-8")


# -- what goes in comes back ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "sk-proj-abc123",
        "-----BEGIN PRIVATE KEY-----\nline1\nline2\n-----END-----\nAção çü",
        json.dumps({"type": "service_account", "private_key": "-----BEGIN-----\nx\n"}),
    ],
)
def test_a_value_comes_back_exactly(home: Home, value: str) -> None:
    vault = _vault(home)

    vault.set("SOME_SECRET", value)

    assert vault.get("SOME_SECRET") == value
    assert _vault(home).get("SOME_SECRET") == value  # a new process finds it too


def test_an_absent_name_is_none(home: Home) -> None:
    assert _vault(home).get("NOT_SET") is None


def test_setting_again_replaces_and_deleting_says_whether_it_existed(home: Home) -> None:
    vault = _vault(home)
    vault.set("KEY", "old")
    vault.set("KEY", "new")

    assert vault.get("KEY") == "new"
    assert vault.delete("KEY") is True
    assert vault.delete("KEY") is False
    assert vault.get("KEY") is None


def test_no_value_is_in_the_file_but_every_name_is(home: Home) -> None:
    vault = _vault(home)
    vault.set("TOKEN", "value-super-secret-xyz")

    text = vault.path.read_text(encoding="utf-8")

    assert "value-super-secret-xyz" not in text
    assert "TOKEN" in text


def test_names_are_listed_sorted_without_the_key(home: Home) -> None:
    secret, _ = _new_key()
    _vault(home, VaultSettings(key=SecretText(secret))).set("B", "1")
    _vault(home, VaultSettings(key=SecretText(secret))).set("A", "2")

    without_key = _vault(home)

    assert without_key.names() == ["A", "B"]


def test_the_time_of_each_write_is_utc(home: Home) -> None:
    vault = _vault(home)
    vault.set("KEY", "value")

    updated_at = vault.updated_at("KEY")

    assert updated_at is not None
    parse_utc(updated_at)
    assert vault.updated_at("MISSING") is None


def test_each_entry_is_a_standard_age_file_that_opens_without_atlas(home: Home) -> None:
    secret, _ = _new_key()
    _vault(home, VaultSettings(key=SecretText(secret))).set("OPENAI_API_KEY", "sk-proj-abc")

    ciphertext = base64.b64decode(
        _document(_vault(home))["entries"]["OPENAI_API_KEY"]["ciphertext"]
    )

    assert ciphertext.startswith(b"age-encryption.org/v1\n")
    identity = pyrage.x25519.Identity.from_str(secret)
    assert pyrage.decrypt(ciphertext, [identity]) == b"sk-proj-abc"


@pytest.mark.parametrize("name", ["", "has space", "../escape", "-dash-first", "x" * 129])
def test_a_bad_name_is_refused(home: Home, name: str) -> None:
    with pytest.raises(InvalidSecretNameError):
        _vault(home).set(name, "value")


def test_an_empty_value_is_refused(home: Home) -> None:
    with pytest.raises(SecretsError, match="empty"):
        _vault(home).set("KEY", "")


# -- damage stays local, and is never written over ------------------------------------------------


def test_a_damaged_entry_costs_only_itself(home: Home) -> None:
    vault = _vault(home)
    vault.set("SENTRY_AUTH_TOKEN", "sntryu_xxx")
    vault.set("OPENAI_API_KEY", "sk-ok")
    _damage(vault, "SENTRY_AUTH_TOKEN")

    with pytest.raises(SecretUnreadableError, match="SENTRY_AUTH_TOKEN"):
        vault.get("SENTRY_AUTH_TOKEN")
    assert vault.get("OPENAI_API_KEY") == "sk-ok"
    assert list(vault.verify()) == ["SENTRY_AUTH_TOKEN"]


def test_broken_base64_is_an_unreadable_entry(home: Home) -> None:
    vault = _vault(home)
    vault.set("KEY", "value")
    document = _document(vault)
    document["entries"]["KEY"]["ciphertext"] = "this-is-not-base64-!!!"
    vault.path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(SecretUnreadableError):
        vault.get("KEY")


def test_a_damaged_file_is_an_error_and_is_never_written_over(home: Home) -> None:
    vault = _vault(home)
    vault.set("KEY", "value")
    vault.path.write_text("{ this is not json", encoding="utf-8")
    before = vault.path.read_bytes()

    with pytest.raises(VaultDamagedError, match="will not write over it"):
        vault.set("NEW", "value")
    with pytest.raises(VaultDamagedError):
        vault.names()
    assert vault.path.read_bytes() == before  # V3 read it as empty and the next write erased it
    assert "damaged" in (vault.problem() or "")


def test_a_vault_from_a_newer_atlas_is_refused(home: Home) -> None:
    vault = _vault(home)
    vault.set("KEY", "value")
    document = _document(vault)
    vault.path.write_text(json.dumps({**document, "format": 2}), encoding="utf-8")

    with pytest.raises(VaultDamagedError, match="newer Atlas"):
        vault.get("KEY")


# -- where the key comes from --------------------------------------------------------------------


def test_the_first_secret_creates_the_home_key_in_a_private_folder(home: Home) -> None:
    _vault(home).set("KEY", "value")

    key_file = home.secrets_folder / HOME_KEY_FILE
    assert key_file.is_file()
    assert is_private(home.secrets_folder)
    assert b"AGE-SECRET-KEY" not in key_file.read_bytes() or sys.platform != "win32"
    if sys.platform != "win32":
        assert key_file.stat().st_mode & 0o777 == 0o600


def test_the_key_from_the_environment_wins_and_no_home_key_is_made(home: Home) -> None:
    secret, public = _new_key()

    _vault(home, VaultSettings(key=SecretText(secret))).set("KEY", "value")

    assert _document(_vault(home))["recipient"] == public
    assert not (home.secrets_folder / HOME_KEY_FILE).exists()


def test_a_key_file_in_age_keygen_format_opens_the_vault(home: Home, tmp_path: Path) -> None:
    secret, public = _new_key()
    key_file = tmp_path / "atlas_vault_key.txt"
    key_file.write_text(f"# created: today\n# public key: {public}\n{secret}\n", encoding="utf-8")
    settings = VaultSettings(key_file=key_file)

    _vault(home, settings).set("KEY", "value")

    assert _vault(home, settings).get("KEY") == "value"
    assert _document(_vault(home))["recipient"] == public


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (None, "does not exist"),
        ("# only a comment\n", "no line starting with AGE-SECRET-KEY-1"),
        ("AGE-SECRET-KEY-1NOTVALID\n", "does not hold a valid age key"),
    ],
)
def test_a_broken_key_file_is_named_with_its_problem(
    home: Home, tmp_path: Path, content: str | None, message: str
) -> None:
    key_file = tmp_path / "key.txt"
    if content is not None:
        key_file.write_text(content, encoding="utf-8")

    with pytest.raises(VaultLockedError, match=message) as raised:
        _vault(home, VaultSettings(key_file=key_file)).set("KEY", "value")
    assert str(key_file) in str(raised.value)


def test_without_its_key_the_vault_says_so_and_makes_no_new_key(home: Home) -> None:
    secret, _ = _new_key()
    _vault(home, VaultSettings(key=SecretText(secret))).set("KEY", "value")
    keyless = _vault(home)

    with pytest.raises(VaultLockedError, match=KEY_FILE_VARIABLE) as raised:
        keyless.get("KEY")
    assert not isinstance(raised.value, SecretUnreadableError)
    assert keyless.get("NOT_SET") is None  # "not set" stays distinct from "cannot open"
    with pytest.raises(VaultLockedError):
        keyless.set("OTHER", "value")
    assert not (home.secrets_folder / HOME_KEY_FILE).exists()
    assert KEY_FILE_VARIABLE in (keyless.problem() or "")


def test_the_key_of_another_vault_is_named_before_decrypting_and_writes_nothing(
    home: Home,
) -> None:
    first, first_public = _new_key()
    other, _ = _new_key()
    _vault(home, VaultSettings(key=SecretText(first))).set("KEY", "value")
    before = _vault(home).path.read_bytes()
    wrong = _vault(home, VaultSettings(key=SecretText(other)))

    assert "not this vault's key" in (wrong.problem() or "")
    assert first_public in (wrong.problem() or "")
    with pytest.raises(VaultLockedError):
        wrong.set("NEW", "value")
    assert wrong.path.read_bytes() == before


def test_a_home_key_others_can_open_is_not_used(home: Home) -> None:
    vault = _vault(home)
    vault.set("KEY", "value")
    if sys.platform == "win32":
        subprocess.run(  # noqa: S603 - give Everyone (S-1-1-0) read access
            ["icacls", str(home.secrets_folder), "/grant", "*S-1-1-0:(R)"],  # noqa: S607
            check=True,
            capture_output=True,
        )
    else:
        home.secrets_folder.chmod(0o755)

    with pytest.raises(VaultLockedError, match="can be opened by other accounts"):
        _vault(home).get("KEY")


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI exists only on Windows")
def test_a_damaged_windows_key_says_which_file_and_why(home: Home) -> None:
    _vault(home).set("KEY", "value")
    (home.secrets_folder / PROTECTED_KEY_FILE).write_bytes(b"not a DPAPI blob")

    with pytest.raises(VaultLockedError, match="cannot be opened by this Windows account"):
        _vault(home).get("KEY")


@pytest.mark.skipif(sys.platform == "win32", reason="the case of a Windows key elsewhere")
def test_a_windows_key_elsewhere_says_where_it_opens(home: Home) -> None:
    home.secrets_folder.mkdir(mode=0o700)
    (home.secrets_folder / PROTECTED_KEY_FILE).write_bytes(b"a DPAPI blob")

    with pytest.raises(VaultLockedError, match="opens only there"):
        _vault(home).set("KEY", "value")


# -- many writers, and the logs ------------------------------------------------------------------


def test_writers_in_separate_processes_lose_nothing_and_share_one_key(home: Home) -> None:
    code = textwrap.dedent(
        """
        import sys
        from pathlib import Path
        from atlas.home import Redactor, VaultSettings
        from atlas.secrets import Vault

        vault = Vault(Path(sys.argv[1]), VaultSettings(), Redactor())
        for index in range(10):
            vault.set(f"WRITER_{sys.argv[2]}_{index}", f"value-{sys.argv[2]}-{index}")
        """
    )
    folder = str(home.secrets_folder)

    writers = [
        subprocess.Popen([sys.executable, "-c", code, folder, str(writer)])  # noqa: S603
        for writer in range(4)
    ]

    assert [writer.wait(timeout=120) for writer in writers] == [0, 0, 0, 0]
    vault = _vault(home)
    assert len(vault.names()) == 40
    assert vault.verify() == {}
    assert vault.get("WRITER_3_9") == "value-3-9"
    key_files = {path.name for path in home.secrets_folder.iterdir() if path.name.startswith("key")}
    assert key_files == {HOME_KEY_FILE}


def test_keep_stores_once_and_then_gives_back_what_it_holds(home: Home) -> None:
    redactor = Redactor()
    vault = _vault(home, redactor=redactor)

    assert vault.keep("TOKEN", "first") == "first"
    assert vault.keep("TOKEN", "second") == "first"
    assert vault.get("TOKEN") == "first"
    assert redactor.redact("first") == REDACTED
    with pytest.raises(SecretsError, match="empty"):
        vault.keep("OTHER", "")


def test_processes_keeping_one_name_at_once_all_get_the_same_secret(home: Home) -> None:
    # The server starting and `atlas server token` run together in a container: the read and the
    # write must share the lock, or each would keep a token of its own.
    code = textwrap.dedent(
        """
        import sys
        from pathlib import Path
        from atlas.home import Redactor, VaultSettings
        from atlas.secrets import Vault

        vault = Vault(Path(sys.argv[1]), VaultSettings(), Redactor())
        sys.stdout.write(vault.keep("TOKEN", f"made-by-{sys.argv[2]}"))
        """
    )
    folder = str(home.secrets_folder)

    keepers = [
        subprocess.Popen(  # noqa: S603
            [sys.executable, "-c", code, folder, str(keeper)], stdout=subprocess.PIPE, text=True
        )
        for keeper in range(4)
    ]

    kept = {keeper.communicate(timeout=120)[0] for keeper in keepers}
    assert kept == {_vault(home).get("TOKEN")}


def test_every_secret_read_or_written_is_hidden_from_the_logs(home: Home) -> None:
    writer_logs, reader_logs = Redactor(), Redactor()

    _vault(home, redactor=writer_logs).set("KEY", "sk-proj-written")
    _vault(home, redactor=reader_logs).get("KEY")

    assert writer_logs.redact("sent sk-proj-written") == f"sent {REDACTED}"
    assert reader_logs.redact("got sk-proj-written") == f"got {REDACTED}"
