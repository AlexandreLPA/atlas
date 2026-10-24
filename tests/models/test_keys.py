"""`atlas models check`: whether each provider takes its key, asked where it spends nothing.

Ported from V3's provider_keys verdicts (valid, refused, out of quota, network failure), whose tests
patched `requests.get`. Here the real SDK asks the fake server over HTTP, and the command runs as
the user runs it, on a temporary home.
"""

import pytest

from atlas.__main__ import main
from atlas.home import Home, ModelSettings
from atlas.models import KeyCheck, KeyStatus, ModelKeyError
from atlas.secrets import Vault
from fake_model import FakeError, FakeModelServer
from model_support import Connect, Run


def test_a_valid_key_is_checked_where_it_costs_nothing(
    fake: FakeModelServer, connect: Connect, run: Run
):
    models = connect(fake, "openrouter", "gemini")

    async def scenario() -> list[KeyCheck]:
        return [await models.check_key("openrouter"), await models.check_key("gemini")]

    openrouter, gemini = run(models, scenario)

    assert (openrouter.status, openrouter.detail) == (KeyStatus.VALID, "The key opens OpenRouter.")
    assert gemini.status is KeyStatus.VALID
    # OpenRouter's /models answers without a key, so it is asked on /key.
    assert [(request.method, request.path) for request in fake.requests] == [
        ("GET", "/v1/key"),
        ("GET", "/v1/models"),
    ]
    assert fake.bodies("/v1/chat/completions") == []


@pytest.mark.parametrize(
    ("error", "status", "usable"),
    [
        (FakeError(401, "Incorrect API key."), KeyStatus.REFUSED, False),
        (FakeError(402, "Insufficient credits."), KeyStatus.NO_CREDIT, False),
        (FakeError(429, "Slow down."), KeyStatus.LIMITED, True),
        (
            FakeError(429, "Quota exceeded.", google="RESOURCE_EXHAUSTED", retry_delay="80822s"),
            KeyStatus.NO_CREDIT,
            False,
        ),
        (FakeError(500, "Broken.", type="server_error"), KeyStatus.UNEXPECTED, False),
    ],
)
def test_each_answer_is_one_verdict(
    fake: FakeModelServer,
    connect: Connect,
    run: Run,
    error: FakeError,
    status: KeyStatus,
    usable: bool,
):
    fake.refuse_check(error)
    models = connect(fake, "gemini")

    check = run(models, lambda: models.check_key("gemini"))

    assert (check.status, check.usable) == (status, usable)
    assert len(fake.requests) == 1  # a check is never retried


def test_a_wrong_key_is_refused(fake: FakeModelServer, connect: Connect, run: Run):
    models = connect(fake, "gemini", key="not-the-key")
    check = run(models, lambda: models.check_key("gemini"))
    assert check.status is KeyStatus.REFUSED


def test_a_missing_key_asks_nothing(fake: FakeModelServer, connect: Connect, run: Run):
    models = connect(fake, "gemini", key=None)
    check = run(models, lambda: models.check_key("gemini"))
    assert check.status is KeyStatus.MISSING
    assert "atlas secrets set GEMINI_API_KEY" in check.detail
    assert fake.requests == []


def test_an_unreachable_provider_says_nothing_about_the_key(connect: Connect, run: Run):
    # V3: a Wi-Fi drop is not a verdict on the key.
    models = connect(None, "gemini")
    check = run(models, lambda: models.check_key("gemini"))
    assert check.status is KeyStatus.UNREACHABLE
    assert "says nothing about the key" in check.detail


def test_a_local_server_is_checked_for_an_answer(connect: Connect, run: Run):
    with FakeModelServer(key=None) as local:
        models = connect(local, "ollama")
        check = run(models, lambda: models.check_key("ollama"))
    assert check.status is KeyStatus.VALID
    assert check.detail == f"Ollama answers at {local.base_url}."


# -- the command ---------------------------------------------------------------------------------


def test_the_command_checks_every_provider_with_a_key(
    fake: FakeModelServer, connect: Connect, home: Home, capsys: pytest.CaptureFixture[str]
):
    connect(fake, "openrouter", "gemini")

    exit_code = main(["models", "check"], {"ATLAS_HOME": str(home.root)})

    assert exit_code == 0
    assert capsys.readouterr().out.splitlines() == [
        "openrouter\tvalid\tThe key opens OpenRouter.",
        "gemini\tvalid\tThe key opens Google Gemini.",
    ]


def test_the_command_fails_when_a_key_is_refused(
    fake: FakeModelServer, connect: Connect, home: Home, capsys: pytest.CaptureFixture[str]
):
    connect(fake, "gemini", key="not-the-key")

    exit_code = main(["models", "check", "gemini"], {"ATLAS_HOME": str(home.root)})

    assert exit_code == 1
    assert capsys.readouterr().out.startswith("gemini\trefused\t")


def test_the_command_adds_the_provider_the_settings_chose(
    fake: FakeModelServer, connect: Connect, home: Home, capsys: pytest.CaptureFixture[str]
):
    connect(fake, "gemini", key=None, settings=ModelSettings())
    environ = {"ATLAS_HOME": str(home.root), "ATLAS_MODEL_PROVIDER": "gemini"}

    exit_code = main(["models", "check"], environ)

    assert exit_code == 1
    assert capsys.readouterr().out.startswith("gemini\tmissing\t")


def test_the_command_without_any_key_says_how_to_store_one(
    home: Home, capsys: pytest.CaptureFixture[str]
):
    exit_code = main(["models", "check"], {"ATLAS_HOME": str(home.root)})

    assert exit_code == 1
    assert "atlas secrets set OPENAI_API_KEY" in capsys.readouterr().err


def test_the_command_refuses_an_unknown_provider(home: Home, capsys: pytest.CaptureFixture[str]):
    exit_code = main(["models", "check", "deepseek"], {"ATLAS_HOME": str(home.root)})

    assert exit_code == 1
    assert "There is no provider 'deepseek'" in capsys.readouterr().err


# -- the key screen ------------------------------------------------------------------------------


def test_the_key_screen_stores_a_key_the_provider_takes(
    fake: FakeModelServer, connect: Connect, run: Run, vault: Vault
):
    models = connect(fake, "gemini", key=None)

    found = run(models, lambda: models.connect("gemini", "  fake-key\n"))

    assert (found.status, found.detail) == (KeyStatus.VALID, "The key opens Google Gemini.")
    assert vault.get("GEMINI_API_KEY") == "fake-key"  # what was pasted around it is not the key
    assert [request.path for request in fake.requests] == ["/v1/models"]
    assert models.default().provider == "gemini"  # the next turn runs on it


def test_the_key_screen_keeps_out_only_a_key_the_provider_refuses(
    fake: FakeModelServer, connect: Connect, run: Run, vault: Vault
):
    models = connect(fake, "gemini", key=None)

    found = run(models, lambda: models.connect("gemini", "not-the-key"))

    assert found.status is KeyStatus.REFUSED
    assert vault.get("GEMINI_API_KEY") is None


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (FakeError(402, "Insufficient credits."), KeyStatus.NO_CREDIT),
        (FakeError(429, "Slow down."), KeyStatus.LIMITED),
        (FakeError(500, "Broken.", type="server_error"), KeyStatus.UNEXPECTED),
    ],
)
def test_the_key_screen_stores_a_key_whose_check_says_something_else(
    fake: FakeModelServer,
    connect: Connect,
    run: Run,
    vault: Vault,
    error: FakeError,
    status: KeyStatus,
):
    """Proxies and flaky providers refuse good keys, so there is no probe: only a refusal keeps a
    key out, and the check's words say what was found."""
    fake.refuse_check(error)
    models = connect(fake, "gemini", key=None)

    found = run(models, lambda: models.connect("gemini", "fake-key"))

    assert found.status is status
    assert vault.get("GEMINI_API_KEY") == "fake-key"


def test_the_key_screen_stores_a_key_when_the_provider_is_out_of_reach(
    connect: Connect, run: Run, vault: Vault
):
    models = connect(None, "gemini", key=None)

    found = run(models, lambda: models.connect("gemini", "fake-key"))

    assert found.status is KeyStatus.UNREACHABLE
    assert vault.get("GEMINI_API_KEY") == "fake-key"


def test_the_key_screen_refuses_an_empty_key_and_a_provider_that_takes_none(
    connect: Connect, run: Run, vault: Vault
):
    models = connect(None, "gemini", "ollama", key=None)

    with pytest.raises(ModelKeyError, match="empty"):
        run(models, lambda: models.connect("gemini", "  "))
    with pytest.raises(ModelKeyError, match="takes no key"):
        run(models, lambda: models.connect("ollama", "x"))
    assert vault.names() == []


def test_the_key_screen_lists_the_providers_that_take_a_key(connect: Connect, vault: Vault):
    models = connect(None, "gemini", key=None)
    vault.set("OPENROUTER_API_KEY", "stored")

    listed = [(provider.id, provider.key_page, stored) for provider, stored in models.keyed()]

    assert listed == [
        ("openai", "https://platform.openai.com/api-keys", False),
        ("openrouter", "https://openrouter.ai/keys", True),
        ("gemini", "https://aistudio.google.com/app/apikey", False),
        ("anthropic", "https://platform.claude.com/settings/keys", False),
    ]
