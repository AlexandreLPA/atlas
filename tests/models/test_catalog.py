"""The model catalog: what ships with Atlas, what the user's models.toml adds, what stays unknown.

Ported from V3's test_model_factory.py and the capability tests in it. The shipped files are read as
installed, and the user's file is a real file in a temporary home.
"""

from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from atlas.home import open_home, parse_utc
from atlas.models import (
    CatalogError,
    EffortParameter,
    Price,
    PriceTier,
    UnknownProviderError,
    Wire,
    load_catalog,
    native_tool_search,
)
from atlas.models.store import _DATA  # pyright: ignore[reportPrivateUsage]


@pytest.fixture
def models_file(tmp_path: Path) -> Path:
    return open_home(tmp_path / "home").models_file


def test_the_built_in_providers_and_their_quirks(models_file: Path) -> None:
    catalog = load_catalog(models_file)

    assert set(catalog.providers) == {
        "openai",
        "openrouter",
        "gemini",
        "anthropic",
        "ollama",
        "vllm",
    }
    anthropic = catalog.provider("anthropic")
    assert (anthropic.wire, anthropic.key, anthropic.catalog) == (
        Wire.MESSAGES,
        "ANTHROPIC_API_KEY",
        "anthropic",
    )
    assert anthropic.trailing_assistant is False  # prefill is a 400 on the current models
    openai = catalog.provider("openai")
    assert (openai.wire, openai.key, openai.catalog) == (Wire.RESPONSES, "OPENAI_API_KEY", "openai")
    gemini = catalog.provider("gemini")
    assert gemini.wire is Wire.CHAT
    assert gemini.replay == {"extra_content"}
    assert gemini.trailing_assistant is False
    openrouter = catalog.provider("openrouter")
    assert openrouter.replay == {"reasoning_details", "extra_content"}
    assert openrouter.effort is EffortParameter.REASONING
    assert openrouter.key_check == "/key"
    ollama = catalog.provider("ollama")
    assert (ollama.key, ollama.free, ollama.catalog) == (None, True, None)


def test_every_default_model_exists_and_can_call_tools(models_file: Path) -> None:
    # V3 offered gpt-5.5-mini for months after the API answered 404 for it: a process that picked
    # it failed in silence. A default model must be one the catalog knows, and an agent needs tools.
    catalog = load_catalog(models_file)
    for provider in catalog.providers.values():
        if provider.default_model is None:
            continue
        info = catalog.model(provider, provider.default_model)
        assert info is not None, provider.id
        assert info.tool_call is True, provider.id


def test_anthropic_models_have_their_own_ids_and_a_price_for_the_hour_long_cache(
    models_file: Path,
) -> None:
    """Anthropic's ids use hyphens (`claude-opus-5-5`; turning dots into hyphens misses), and
    models.dev prices only the five-minute cache write: the provider's factor prices the hour-long
    one at 2x the input."""
    catalog = load_catalog(models_file)
    anthropic = catalog.provider("anthropic")
    opus = catalog.model(anthropic, "claude-opus-5-5")

    assert anthropic.default_model == "claude-opus-5-5"
    assert opus is not None
    assert opus.price is not None
    assert (opus.price.input, opus.price.cache_read, opus.price.cache_write) == (
        Decimal(4),
        Decimal("0.2"),
        Decimal(5),
    )
    assert opus.price.cache_write_long == Decimal(8)
    assert opus.efforts == ("low", "medium", "high", "xhigh", "max")
    assert catalog.model(anthropic, "claude-opus-5.5") is None


def test_a_known_model_comes_with_models_dev_capability_and_price(models_file: Path) -> None:
    catalog = load_catalog(models_file)
    info = catalog.model(catalog.provider("openai"), "gpt-6-luna")

    assert info is not None
    assert info.source == "models.dev"
    assert (info.context_window, info.max_output_tokens) == (1_050_000, 128_000)
    assert info.tool_call is True
    assert info.reasoning is True
    assert info.efforts is not None
    assert "none" in info.efforts
    assert info.price == Price(
        input=Decimal("0.1"),
        output=Decimal("0.5"),
        cache_read=Decimal("0.01"),
        cache_write=Decimal("0.125"),
        tiers=(
            PriceTier(
                above_input_tokens=272_000,
                input=Decimal("0.2"),
                output=Decimal("0.75"),
                cache_read=Decimal("0.02"),
                cache_write=Decimal("0.25"),
            ),
        ),
    )


def test_a_model_that_refuses_effort_none_says_so(models_file: Path) -> None:
    # V3 kept gpt-6-astra out of its catalog by hand: it accepts no `none`, and without `none`
    # Chat Completions refuses function tools. The data says it now.
    catalog = load_catalog(models_file)
    info = catalog.model(catalog.provider("openai"), "gpt-6-astra")
    assert info is not None
    assert info.efforts is not None
    assert "none" not in info.efforts


def test_the_shipped_cut_credits_models_dev_and_says_when_it_was_taken() -> None:
    text = (_DATA / "models.json").read_text(encoding="utf-8")
    assert '"source": "https://models.dev/api.json"' in text
    assert '"license": "MIT, the models.dev contributors"' in text
    fetched = text.split('"fetched_at": "', 1)[1].split('"', 1)[0]
    assert isinstance(parse_utc(fetched), datetime)


def test_an_unknown_model_has_no_record_rather_than_a_guess(models_file: Path) -> None:
    catalog = load_catalog(models_file)
    assert catalog.model(catalog.provider("openai"), "a-model-nobody-ever-made") is None
    assert catalog.model(catalog.provider("ollama"), "qwen3:32b") is None


def test_an_unknown_provider_is_refused_with_the_known_ones(models_file: Path) -> None:
    catalog = load_catalog(models_file)
    with pytest.raises(UnknownProviderError, match=r"no provider 'deepseek'.*gemini, ollama"):
        catalog.provider("deepseek")


def test_the_user_adds_a_local_model_and_a_provider(models_file: Path) -> None:
    models_file.write_text(
        """
[providers.deepseek]
name = "DeepSeek"
base_url = "https://api.deepseek.com/v1"
key = "DEEPSEEK_API_KEY"

[providers.ollama]
base_url = "http://gpu-box:11434/v1"

[[models]]
provider = "ollama"
id = "qwen3:32b"
limit = { context = 65536, output = 8192 }
tool_call = true

[[models]]
provider = "deepseek"
id = "deepseek-chat"
cost = { input = 0.27, output = 1.10, cache_read = 0.07 }
""",
        encoding="utf-8",
    )
    catalog = load_catalog(models_file)

    deepseek = catalog.provider("deepseek")
    assert (deepseek.base_url, deepseek.key, deepseek.wire) == (
        "https://api.deepseek.com/v1",
        "DEEPSEEK_API_KEY",
        Wire.CHAT,
    )
    ollama = catalog.provider("ollama")
    assert ollama.base_url == "http://gpu-box:11434/v1"
    assert ollama.free is True  # the fields the user did not touch stay
    qwen = catalog.model(ollama, "qwen3:32b")
    assert qwen is not None
    assert (qwen.context_window, qwen.tool_call, qwen.reasoning, qwen.price) == (
        65536,
        True,
        None,
        None,
    )
    assert qwen.source == str(models_file)
    chat = catalog.model(deepseek, "deepseek-chat")
    assert chat is not None
    assert chat.price == Price(Decimal("0.27"), Decimal("1.10"), cache_read=Decimal("0.07"))


def test_the_user_corrects_one_price_and_keeps_the_rest_of_the_record(models_file: Path) -> None:
    models_file.write_text(
        '[[models]]\nprovider = "openai"\nid = "gpt-6-luna"\ncost = { input = 0.05 }\n',
        encoding="utf-8",
    )
    catalog = load_catalog(models_file)
    info = catalog.model(catalog.provider("openai"), "gpt-6-luna")

    assert info is not None
    assert info.source == f"models.dev + {models_file}"
    assert info.price is not None
    assert info.price.input == Decimal("0.05")
    assert info.price.output == Decimal("0.5")
    assert info.price.tiers[0].above_input_tokens == 272_000
    assert info.context_window == 1_050_000


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("[general]\nx = 1\n", r"unknown fields: general\. Known: models, providers"),
        (
            '[providers.openai]\nwire_format = "chat"\n',
            r"\[providers\.openai\] has unknown fields: wire_format\. Known: ask_reasoning",
        ),
        (
            '[[models]]\nprovider = "openai"\nid = "x"\ncontext = 5\n',
            r"#1 has unknown fields: context\. Known: cost, efforts",
        ),
        (
            '[[models]]\nprovider = "openai"\nid = "x"\n'
            "cost = { input = 1, output = 2, cached = 0.1 }\n",
            r"cost has unknown fields: cached",
        ),
        (
            '[[models]]\nprovider = "nobody"\nid = "x"\n',
            r"provider 'nobody', which nobody declared",
        ),
        ('[[models]]\nid = "x"\n', r"needs provider and id"),
        ('[providers.mine]\nkey = "MINE_KEY"\n', r"new provider without base_url"),
        ('[providers.openai]\nwire = "grpc"\n', r"wire is 'grpc'; use one of: chat, responses"),
        ("[[models]\n", r"is not valid TOML"),
    ],
)
def test_the_users_file_is_strict(models_file: Path, text: str, message: str) -> None:
    models_file.write_text(text, encoding="utf-8")
    with pytest.raises(CatalogError, match=message):
        load_catalog(models_file)


def test_correcting_a_model_keeps_the_models_dev_fields_atlas_does_not_read(
    models_file: Path,
) -> None:
    # gemini-3.8-flash's price in models.dev has input_audio; correcting its window refused it.
    models_file.write_text(
        '[[models]]\nprovider = "gemini"\nid = "gemini-3.8-flash"\n'
        "limit = { context = 9000, input = 7000 }\n",
        encoding="utf-8",
    )
    catalog = load_catalog(models_file)
    info = catalog.model(catalog.provider("gemini"), "gemini-3.8-flash")
    assert info is not None
    assert (info.context_window, info.max_input_tokens) == (9000, 7000)
    assert info.price is not None
    assert info.price.input == Decimal("0.75")


def test_a_partial_price_is_no_price(models_file: Path) -> None:
    models_file.write_text(
        '[[models]]\nprovider = "ollama"\nid = "small"\ncost = { input = 0.1 }\n',
        encoding="utf-8",
    )
    catalog = load_catalog(models_file)
    info = catalog.model(catalog.provider("ollama"), "small")
    assert info is not None
    assert info.price is None


def test_a_kind_of_tier_atlas_does_not_know_leaves_the_price_unknown(models_file: Path) -> None:
    # Pricing without a tier Atlas cannot read could come out far too low.
    models_file.write_text(
        '[[models]]\nprovider = "ollama"\nid = "small"\n'
        "cost = { input = 1, output = 2, tiers = "
        '[{ input = 2, output = 4, tier = { type = "night", size = 0 } }] }\n',
        encoding="utf-8",
    )
    catalog = load_catalog(models_file)
    info = catalog.model(catalog.provider("ollama"), "small")
    assert info is not None
    assert info.price is None


def test_native_tool_search_belongs_to_the_responses_api(models_file: Path) -> None:
    catalog = load_catalog(models_file)
    openai = catalog.provider("openai")

    assert native_tool_search(openai, "gpt-6-luna")
    assert native_tool_search(openai, "gpt-5.4-mini")
    assert not native_tool_search(openai, "gpt-5")
    assert not native_tool_search(openai, "gpt-5.40")
    assert not native_tool_search(catalog.provider("openrouter"), "openai/gpt-6-luna")

    models_file.write_text('[providers.gemini]\ntool_search = ["gemini-3"]\n', encoding="utf-8")
    with pytest.raises(CatalogError, match="needs wire"):
        load_catalog(models_file)
