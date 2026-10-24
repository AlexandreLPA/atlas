"""Usage and cost: the cases V3's accounting got wrong before it got them right.

Ported from V3's test_token_accounting.py and the price tests of test_model_factory.py, with the
numbers V3 checked against OpenAI's price page. Nothing here calls a provider: accounting must be
checkable without spending money.
"""

from decimal import Decimal
from pathlib import Path

import pytest

from atlas.home import open_home
from atlas.models import (
    Cost,
    CostSource,
    Finish,
    Price,
    PriceTier,
    load_catalog,
    make_usage,
    price_call,
)
from atlas.models.model import finish_from


def _price(tmp_path: Path, provider: str, model: str) -> Price:
    catalog = load_catalog(open_home(tmp_path / "home").models_file)
    info = catalog.model(catalog.provider(provider), model)
    assert info is not None
    assert info.price is not None
    return info.price


def test_input_reported_without_the_cache_is_corrected() -> None:
    # A provider that reported input without the cache made `input - cache` negative and the
    # call free. When the parts do not fit in the whole, the whole is wrong.
    usage = make_usage(input_tokens=100, output_tokens=10, cache_read_tokens=900)
    assert (usage.input_tokens, usage.cache_read_tokens, usage.new_input_tokens) == (1000, 900, 100)
    # An input that already holds its cache, as every OpenAI-compatible provider reports it, stays.
    usage = make_usage(input_tokens=1500, output_tokens=10, cache_read_tokens=900)
    assert (usage.input_tokens, usage.new_input_tokens) == (1500, 600)


def test_reasoning_is_not_counted_twice() -> None:
    # Reasoning tokens are already inside the output; adding them again doubled o1 and R1.
    usage = make_usage(input_tokens=10, output_tokens=500, reasoning_tokens=400)
    assert (usage.output_tokens, usage.reasoning_tokens) == (500, 400)
    usage = make_usage(input_tokens=10, output_tokens=100, reasoning_tokens=400)
    assert usage.output_tokens == 400


def test_negative_counts_from_a_provider_become_zero() -> None:
    usage = make_usage(input_tokens=-5, output_tokens=-1, cache_read_tokens=-3)
    assert (usage.input_tokens, usage.output_tokens, usage.cache_read_tokens) == (0, 0, 0)


def test_cache_reads_are_charged_at_the_cache_price(tmp_path: Path) -> None:
    price = _price(tmp_path, "openai", "gpt-6-luna")
    full = price_call(
        make_usage(input_tokens=100_000, output_tokens=0), price, reported=None, free=False
    )
    cached = price_call(
        make_usage(input_tokens=100_000, output_tokens=0, cache_read_tokens=100_000),
        price,
        reported=None,
        free=False,
    )
    assert full == Cost(Decimal("0.01"), CostSource.PRICE_LIST)  # 100k at 0.10
    assert cached == Cost(Decimal("0.001"), CostSource.PRICE_LIST)  # 100k at 0.01


def test_openai_charges_cache_writes_above_the_input_price(tmp_path: Path) -> None:
    # GPT-5.6 and later bill a cache write at 1.25 times the input; V3's catalog missed it.
    price = _price(tmp_path, "openai", "gpt-6-luna")
    cost = price_call(
        make_usage(input_tokens=100_000, output_tokens=0, cache_write_tokens=100_000),
        price,
        reported=None,
        free=False,
    )
    assert cost.usd == Decimal("0.0125")  # 100k at 0.125, where new input costs 0.10


def test_the_long_context_tier_is_chosen_per_call(tmp_path: Path) -> None:
    # Past 272,000 input tokens OpenAI reprices the whole request: 2x input, 1.5x output. V3
    # measured its p95 input at 826,114 tokens, so expensive calls live on that side of the line.
    price = _price(tmp_path, "openai", "gpt-5.6-luna")
    below = price_call(
        make_usage(input_tokens=200_000, output_tokens=1_000), price, reported=None, free=False
    )
    above = price_call(
        make_usage(input_tokens=300_000, output_tokens=1_000), price, reported=None, free=False
    )
    assert below.usd == Decimal("0.0412")  # 200k at 0.20 + 1k at 1.20
    assert above.usd == Decimal("0.1218")  # 300k at 0.40 + 1k at 1.80


def test_the_tier_starts_past_its_size_not_at_it() -> None:
    price = Price(
        Decimal(1),
        Decimal(1),
        tiers=(PriceTier(above_input_tokens=1000, input=Decimal(2), output=Decimal(2)),),
    )
    assert price.for_input(1000).input == Decimal(1)
    assert price.for_input(1001).input == Decimal(2)


def test_a_missing_cache_price_means_no_discount_not_a_guessed_one() -> None:
    price = Price(input=Decimal(2), output=Decimal(8))
    cost = price_call(
        make_usage(input_tokens=1_000_000, output_tokens=0, cache_read_tokens=600_000),
        price,
        reported=None,
        free=False,
    )
    assert cost.usd == Decimal(2)


def test_reasoning_at_its_own_price_when_the_model_has_one() -> None:
    price = Price(input=Decimal(0), output=Decimal(1), reasoning=Decimal(3))
    cost = price_call(
        make_usage(input_tokens=0, output_tokens=1_000_000, reasoning_tokens=400_000),
        price,
        reported=None,
        free=False,
    )
    assert cost.usd == Decimal("1.8")  # 600k at 1 + 400k at 3


def test_an_unknown_price_is_unknown_never_zero() -> None:
    # V3's first version priced any unknown model as gpt-4o-mini: o1-pro came out 1000x too cheap.
    usage = make_usage(input_tokens=1_000_000, output_tokens=1_000_000)
    assert price_call(usage, None, reported=None, free=False) == Cost(None, CostSource.UNKNOWN)


def test_a_call_without_reported_usage_has_an_unknown_cost() -> None:
    price = Price(input=Decimal(1), output=Decimal(1))
    assert price_call(None, price, reported=None, free=False).source is CostSource.UNKNOWN


def test_a_local_model_costs_zero_and_the_zero_is_known() -> None:
    cost = price_call(None, None, reported=None, free=True)
    assert cost == Cost(Decimal(0), CostSource.FREE)


def test_the_cost_the_provider_reports_wins_over_the_price_list() -> None:
    price = Price(input=Decimal(100), output=Decimal(100))
    usage = make_usage(input_tokens=1000, output_tokens=1000)
    cost = price_call(usage, price, reported=Decimal("0.0042"), free=False)
    assert cost == Cost(Decimal("0.0042"), CostSource.REPORTED)


@pytest.mark.parametrize(
    ("reason", "has_tool_calls", "finish"),
    [
        ("stop", False, Finish.STOP),
        ("completed", False, Finish.STOP),
        (None, False, Finish.STOP),
        ("stop", True, Finish.TOOL_CALLS),  # some providers say stop after asking for a tool
        ("tool_calls", False, Finish.TOOL_CALLS),
        ("length", False, Finish.LENGTH),
        ("max_output_tokens", False, Finish.LENGTH),
        ("content_filter", False, Finish.FILTERED),
        ("something_new", False, Finish.OTHER),
    ],
)
def test_stop_reasons_in_atlas_words(
    reason: str | None, has_tool_calls: bool, finish: Finish
) -> None:
    assert finish_from(reason, has_tool_calls=has_tool_calls) is finish
