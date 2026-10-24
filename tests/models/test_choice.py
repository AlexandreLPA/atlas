"""What a conversation may pick: the picker's list, the default, and a pick's check.

The catalog is the shipped one plus a real models.toml; the vault is real, in a temporary home.
"""

import pytest

from atlas.home import Home, ModelSettings
from atlas.models import (
    ModelChoice,
    ModelChoiceError,
    ModelKeyError,
    Models,
    UnknownProviderError,
    check_choice,
    clamp_effort,
    default_effort,
    load_catalog,
)
from atlas.secrets import Vault
from model_support import Connect


def test_the_picker_offers_the_providers_with_a_key_and_their_curated_models(
    connect: Connect,
) -> None:
    """Explicit only: a provider whose key the vault lacks is not offered."""
    models = connect(None, "openrouter", "gemini")

    offered = models.offered()

    assert [provider.id for provider in offered] == ["openrouter", "gemini"]
    gemini = offered[1]
    assert gemini.name == "Google Gemini"
    assert gemini.models[0].id == "gemini-3.8-flash"
    assert gemini.models[0].reasoning is True
    assert gemini.models[0].efforts == ("low", "medium", "high")
    assert "anthropic/claude-sonnet-5.5" in [model.id for model in offered[0].models]


def test_a_model_the_user_declares_joins_the_picker_and_a_local_server_with_models_is_offered(
    home: Home, connect: Connect
) -> None:
    home.models_file.write_text(
        '[[models]]\nprovider = "gemini"\nid = "gemini-lab"\nname = "Lab"\ntool_call = true\n\n'
        '[[models]]\nprovider = "ollama"\nid = "qwen-local"\nname = "Qwen"\ntool_call = true\n\n',
        encoding="utf-8",
    )
    models = connect(None, "gemini")

    offered = {provider.id: provider for provider in models.offered()}

    assert [model.id for model in offered["gemini"].models][-1] == "gemini-lab"
    assert [model.id for model in offered["ollama"].models] == ["qwen-local"]
    assert "vllm" not in offered  # no key needed, but nothing declared


def test_the_default_is_the_settings_choice_or_the_first_provider_with_a_key(
    vault: Vault, connect: Connect
) -> None:
    automatic = connect(None, "gemini")
    assert automatic.default() == ModelChoice("gemini", "gemini-3.8-flash", "medium")

    def default(settings: ModelSettings) -> ModelChoice:
        return Models(automatic.catalog, vault, settings).default()

    assert default(ModelSettings(provider="openrouter", effort="low")) == ModelChoice(
        "openrouter", "openai/gpt-6-luna", "low"
    )
    assert default(ModelSettings(provider="gemini", name="gemini-3.7-flash")) == ModelChoice(
        "gemini", "gemini-3.7-flash", "medium"
    )
    with pytest.raises(ModelChoiceError, match=r"without \[model\] provider"):
        default(ModelSettings(name="gemini-3.7-flash"))


def test_an_effort_clamps_to_the_nearest_weaker_level_the_model_takes() -> None:
    """`clamp_effort`: never stronger than asked, never `none` for one that reasons."""
    assert clamp_effort("medium", ("low", "medium", "high")) == "medium"
    assert clamp_effort("medium", ("low", "high", "max")) == "low"
    assert clamp_effort("medium", ("none", "high", "max")) == "high", "nothing weaker but none"
    assert clamp_effort("medium", ()) == "medium", "levels unknown: as asked"
    assert clamp_effort("turbo", ("low", "high")) == "turbo", "no level of the ladder: as asked"


def test_nobody_choosing_an_effort_runs_atlas_default_where_the_model_takes_one(
    home: Home, connect: Connect
) -> None:
    """Decided by the user on 17/10/2026: what Atlas calls runs on medium unless asked otherwise,
    so the picker shows what is sent."""
    home.models_file.write_text(
        '[[models]]\nprovider = "gemini"\nid = "plain"\nname = "Plain"\ntool_call = true\n'
        "reasoning = false\n\n"
        '[[models]]\nprovider = "gemini"\nid = "two"\nname = "Two"\ntool_call = true\n'
        'reasoning = true\nefforts = ["low", "high"]\n\n',
        encoding="utf-8",
    )
    models = connect(None, "gemini")

    assert models.default() == ModelChoice("gemini", "gemini-3.8-flash", "medium")
    assert models.client().effort == "medium"
    assert models.client("gemini", "two").effort == "low", "medium onto its levels"
    assert models.client("gemini", "two", effort="high").effort == "high", "a choice stands"
    assert models.client("gemini", "plain").effort is None, "it does not reason"
    unknown = models.resolved(ModelChoice("gemini", "gemini-unknown-to-the-catalog"))
    assert unknown.effort is None, "an effort it may refuse is never sent"
    assert default_effort(True, None) is None
    assert default_effort(None, ("low", "medium")) == "medium"


def test_a_pick_that_cannot_run_is_refused_with_the_fix(connect: Connect) -> None:
    models = connect(None, "gemini")

    models.check(ModelChoice("gemini", "gemini-3.6-flash", "minimal"))
    models.check(ModelChoice("gemini", "gemini-unknown-to-the-catalog", "whatever"))
    with pytest.raises(ModelChoiceError, match="does not accept the effort 'max'"):
        models.check(ModelChoice("gemini", "gemini-3.8-flash", "max"))
    with pytest.raises(ModelKeyError, match="atlas secrets set OPENAI_API_KEY"):
        models.check(ModelChoice("openai", "gpt-6-luna", None))
    with pytest.raises(UnknownProviderError):
        models.check(ModelChoice("nobody", "x", None))


def test_a_model_that_calls_no_tools_or_reasons_not_takes_no_pick_of_effort(home: Home) -> None:
    home.models_file.write_text(
        '[[models]]\nprovider = "gemini"\nid = "plain"\nname = "Plain"\ntool_call = true\n'
        "reasoning = false\n\n"
        '[[models]]\nprovider = "gemini"\nid = "mute"\nname = "Mute"\ntool_call = false\n\n',
        encoding="utf-8",
    )
    catalog = load_catalog(home.models_file)
    gemini = catalog.provider("gemini")

    check_choice(ModelChoice("gemini", "plain"), catalog.model(gemini, "plain"))
    with pytest.raises(ModelChoiceError, match="does not reason"):
        check_choice(ModelChoice("gemini", "plain", "low"), catalog.model(gemini, "plain"))
    with pytest.raises(ModelChoiceError, match="cannot call tools"):
        check_choice(ModelChoice("gemini", "mute"), catalog.model(gemini, "mute"))
