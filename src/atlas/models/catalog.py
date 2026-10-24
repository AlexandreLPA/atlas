"""The model catalog: the providers Atlas knows and what each model can do and costs.

Three sources, each a data file, none of them code (Atlas V3 kept 1,300 lines of catalog in Python,
where a dated price aged unseen):

- `data/providers.toml`, written by hand: the built-in providers, their address, key and quirks.
- `data/models.json`, a cut of models.dev made by `scripts/update_model_catalog.py`: capability and
  price per model, in models.dev's vocabulary, prices in US dollars per million tokens.
- `ATLAS_HOME/models.toml`, the user's: new providers (DeepSeek, a vLLM on another machine), fields
  that replace a built-in provider's, and models added or corrected (a local model's context window,
  a negotiated price). It wins over the other two, field by field.

**The user's file is strict**, like `config.toml`: an unknown key stops Atlas with the known ones,
because a typo would otherwise be ignored in silence. **Unknown stays unknown**: a model absent
from every source has no `ModelInfo`, and a field nobody declared is `None`.

Pure: this module parses text, and `atlas.models.store` reads the files.
"""

import json
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any, cast

from atlas.models.errors import CatalogError, UnknownProviderError
from atlas.models.model import (
    EffortParameter,
    JSONObject,
    ModelInfo,
    Price,
    PriceTier,
    Provider,
    Wire,
)

PROVIDER_FIELDS = frozenset(
    {
        "name",
        "base_url",
        "key",
        "wire",
        "catalog",
        "replay",
        "effort",
        "trailing_assistant",
        "free",
        "key_check",
        "key_page",
        "cache_write_long",
        "max_output",
        "default_model",
        "models",
        "ask_reasoning",
        "tool_search",
    }
)
MODEL_FIELDS = frozenset(
    {
        "provider",
        "id",
        "name",
        "tool_call",
        "reasoning",
        "efforts",
        "modalities",
        "limit",
        "cost",
        "last_updated",
        "status",
    }
)
LIMIT_FIELDS = frozenset({"context", "input", "output"})
COST_FIELDS = frozenset(
    {"input", "output", "cache_read", "cache_write", "cache_write_long", "reasoning", "tiers"}
)
TIER_FIELDS = frozenset({"input", "output", "cache_read", "cache_write", "tier"})

MODELS_DEV = "models.dev"


@dataclass(frozen=True, slots=True)
class Catalog:
    providers: Mapping[str, Provider]
    #: models.dev's records, by (models.dev provider, model id).
    shipped: Mapping[tuple[str, str], ModelInfo]
    #: The user's records, by (Atlas provider, model id), already merged over models.dev's.
    added: Mapping[tuple[str, str], ModelInfo]

    def provider(self, provider_id: str) -> Provider:
        found = self.providers.get(provider_id)
        if found is None:
            raise UnknownProviderError(
                f"There is no provider {provider_id!r}. Known providers: "
                f"{', '.join(sorted(self.providers))}. Declare a new one under "
                f"[providers.{provider_id}] in models.toml."
            )
        return found

    def model(self, provider: Provider, model_id: str) -> ModelInfo | None:
        """What the catalog knows about a model of this provider, or `None`. A provider's
        `cache_write_long` prices the hour-long cache write where the record does not."""
        found = self.added.get((provider.id, model_id))
        if found is None and provider.catalog is not None:
            found = self.shipped.get((provider.catalog, model_id))
        factor = provider.cache_write_long
        if found is None or found.price is None or factor is None:
            return found
        price = found.price
        if price.cache_write_long is not None:
            return found
        return replace(found, price=replace(price, cache_write_long=price.input * factor))


def parse_catalog(
    providers_text: str,
    models_text: str,
    user_text: str | None = None,
    user_where: str = "models.toml",
) -> Catalog:
    """The catalog from the shipped files and, when there is one, the user's models.toml."""
    try:
        shipped_providers = tomllib.loads(providers_text, parse_float=Decimal).get("providers")
        shipped_models = json.loads(models_text, parse_float=Decimal)
    except (tomllib.TOMLDecodeError, ValueError) as error:
        raise CatalogError(
            f"The model catalog shipped with Atlas is damaged ({error}). Reinstall Atlas."
        ) from error
    user = _read_user(user_text, user_where) if user_text is not None else {}
    user_providers = _table(user.get("providers", {}), "providers", user_where)

    raw_providers = {
        name: _table(table, name, "providers.toml")
        for name, table in _table(shipped_providers, "providers", "providers.toml").items()
    }
    for name, table in user_providers.items():
        fields = _table(table, f"[providers.{name}]", user_where)
        _refuse_unknown(fields, PROVIDER_FIELDS, f"{user_where} [providers.{name}]")
        if name not in raw_providers and "base_url" not in fields:
            raise CatalogError(
                f"{user_where} [providers.{name}] declares a new provider without base_url. "
                "Give its OpenAI-compatible address, such as https://api.deepseek.com/v1."
            )
        raw_providers[name] = {**raw_providers.get(name, {}), **fields}
    providers = {
        name: _provider(name, fields, user_where if name in user_providers else "providers.toml")
        for name, fields in raw_providers.items()
    }

    catalogs = _table(
        _table(shipped_models, "models.json", "models.json").get("catalogs"),
        "catalogs",
        "models.json",
    )
    raw_shipped: dict[tuple[str, str], dict[str, Any]] = {}
    for catalog_id, models in catalogs.items():
        for model_id, entry in _table(models, catalog_id, "models.json").items():
            raw_shipped[(catalog_id, model_id)] = _table(entry, model_id, "models.json")
    shipped = {
        key: _model(key[1], entry, MODELS_DEV, "models.json") for key, entry in raw_shipped.items()
    }

    added: dict[tuple[str, str], ModelInfo] = {}
    for position, entry in enumerate(_list(user.get("models", []), "models", user_where), 1):
        where = f"{user_where} [[models]] #{position}"
        fields = _table(entry, "[[models]]", user_where)
        _check_user_model(fields, where)
        provider_id, model_id = fields.get("provider"), fields.get("id")
        if not isinstance(provider_id, str) or not isinstance(model_id, str):
            raise CatalogError(f'{where} needs provider and id, such as provider = "ollama".')
        if provider_id not in providers:
            raise CatalogError(
                f"{where} names the provider {provider_id!r}, which nobody declared. Known: "
                f"{', '.join(sorted(providers))}."
            )
        catalog_id = providers[provider_id].catalog
        base = raw_shipped.get((catalog_id, model_id), {}) if catalog_id else {}
        merged = _merge(base, {k: v for k, v in fields.items() if k not in ("provider", "id")})
        source = f"{MODELS_DEV} + {user_where}" if base else user_where
        added[(provider_id, model_id)] = _model(model_id, merged, source, where)
    return Catalog(providers, shipped, added)


def with_providers(
    catalog: Catalog, tables: Mapping[str, Mapping[str, Any]], where: str
) -> Catalog:
    """`catalog` with the providers a plugin adds (step 16l), each written as `models.toml`'s
    `[providers.<id>]`. A plugin adds providers and never changes one: an id Atlas or the user
    already has is refused. Raises `CatalogError`."""
    providers = dict(catalog.providers)
    for provider_id, fields in tables.items():
        if provider_id in catalog.providers:
            raise CatalogError(
                f"{where} adds the provider {provider_id}, which Atlas or models.toml already "
                "has; a plugin adds new providers only."
            )
        table = dict(fields)
        _refuse_unknown(table, PROVIDER_FIELDS, f"{where} [providers.{provider_id}]")
        providers[provider_id] = _provider(provider_id, table, where)
    return replace(catalog, providers=providers)


# -- providers -----------------------------------------------------------------------------------


def _provider(provider_id: str, fields: dict[str, Any], where: str) -> Provider:
    at = f"{where} [providers.{provider_id}]"
    wire = _choice(fields.get("wire", Wire.CHAT.value), tuple(Wire), f"{at} wire")
    effort = _choice(
        fields.get("effort", EffortParameter.REASONING_EFFORT.value),
        tuple(EffortParameter),
        f"{at} effort",
    )
    key_check = _text(fields.get("key_check", "/models"), f"{at} key_check")
    if not key_check.startswith("/"):
        raise CatalogError(f"{at} key_check must be a path under base_url, such as /models.")
    replay = _list(fields.get("replay", []), f"{at} replay", where)
    tool_search = _list(fields.get("tool_search", []), f"{at} tool_search", where)
    offered = _list(fields.get("models", []), f"{at} models", where)
    if tool_search and wire != Wire.RESPONSES.value:
        raise CatalogError(
            f'{at} tool_search needs wire = "responses": only the Responses API has its own '
            "tool search."
        )
    return Provider(
        id=provider_id,
        name=_text(fields.get("name", provider_id), f"{at} name"),
        base_url=_text(fields.get("base_url"), f"{at} base_url"),
        key=_optional_text(fields.get("key"), f"{at} key"),
        wire=Wire(wire),
        catalog=_optional_text(fields.get("catalog"), f"{at} catalog"),
        replay=frozenset(_text(item, f"{at} replay") for item in replay),
        effort=EffortParameter(effort),
        trailing_assistant=_flag(
            fields.get("trailing_assistant", True), f"{at} trailing_assistant"
        ),
        free=_flag(fields.get("free", False), f"{at} free"),
        key_check=key_check,
        key_page=_optional_text(fields.get("key_page"), f"{at} key_page"),
        cache_write_long=_optional_money(fields.get("cache_write_long"), f"{at} cache_write_long"),
        max_output=_count(fields.get("max_output"), f"{at} max_output"),
        default_model=_optional_text(fields.get("default_model"), f"{at} default_model"),
        models=tuple(_text(item, f"{at} models") for item in offered),
        ask_reasoning=cast(
            "JSONObject", _table(fields.get("ask_reasoning", {}), f"{at} ask_reasoning", where)
        ),
        tool_search=tuple(_text(item, f"{at} tool_search") for item in tool_search),
    )


# -- models --------------------------------------------------------------------------------------


def _check_user_model(fields: dict[str, Any], where: str) -> None:
    """Refuse unknown fields in what the user wrote, and only there.

    A user's entry is merged over models.dev's, which carries fields Atlas does not read (the price
    of audio input, say); checking the merged record refused models.dev's own fields as typos.
    """
    _refuse_unknown(fields, MODEL_FIELDS, where)
    _refuse_unknown(_table(fields.get("limit", {}), "limit", where), LIMIT_FIELDS, f"{where} limit")
    cost = _table(fields.get("cost", {}), "cost", where)
    _refuse_unknown(cost, COST_FIELDS, f"{where} cost")
    for tier in _list(cost.get("tiers", []), "cost.tiers", where):
        _refuse_unknown(_table(tier, "cost.tiers", where), TIER_FIELDS, f"{where} cost.tiers")


def _model(model_id: str, fields: dict[str, Any], source: str, where: str) -> ModelInfo:
    at = f"{where} model {model_id!r}"
    limit = _table(fields.get("limit", {}), f"{at} limit", where)
    efforts = fields.get("efforts")
    modalities = _table(fields.get("modalities", {}), f"{at} modalities", where)
    return ModelInfo(
        id=model_id,
        name=_text(fields.get("name", model_id), f"{at} name"),
        context_window=_count(limit.get("context"), f"{at} limit.context"),
        max_input_tokens=_count(limit.get("input"), f"{at} limit.input"),
        max_output_tokens=_count(limit.get("output"), f"{at} limit.output"),
        tool_call=_optional_flag(fields.get("tool_call"), f"{at} tool_call"),
        reasoning=_optional_flag(fields.get("reasoning"), f"{at} reasoning"),
        efforts=None
        if efforts is None
        else tuple(_text(item, f"{at} efforts") for item in _list(efforts, f"{at} efforts", where)),
        input_modalities=tuple(
            _text(item, f"{at} modalities")
            for item in _list(modalities.get("input", []), f"{at} modalities.input", where)
        ),
        price=_price(fields.get("cost"), at),
        source=source,
    )


def _price(cost: object, at: str) -> Price | None:
    """The price, or `None` when it is not known in full. A partial price is not a price."""
    if cost is None:
        return None
    fields = _table(cost, f"{at} cost", at)
    if "input" not in fields or "output" not in fields:
        return None
    tiers: list[PriceTier] = []
    for entry in _list(fields.get("tiers", []), f"{at} cost.tiers", at):
        tier = _table(entry, f"{at} cost.tiers", at)
        rule = _table(tier.get("tier", {}), f"{at} cost.tiers.tier", at)
        if rule.get("type") != "context":
            # A kind of tier Atlas does not know: pricing without it could be far too low.
            return None
        tiers.append(
            PriceTier(
                above_input_tokens=_count(rule.get("size"), f"{at} cost.tiers.tier.size") or 0,
                input=_money(tier.get("input"), f"{at} cost.tiers.input"),
                output=_money(tier.get("output"), f"{at} cost.tiers.output"),
                cache_read=_optional_money(tier.get("cache_read"), f"{at} cost.tiers.cache_read"),
                cache_write=_optional_money(
                    tier.get("cache_write"), f"{at} cost.tiers.cache_write"
                ),
            )
        )
    return Price(
        input=_money(fields["input"], f"{at} cost.input"),
        output=_money(fields["output"], f"{at} cost.output"),
        cache_read=_optional_money(fields.get("cache_read"), f"{at} cost.cache_read"),
        cache_write=_optional_money(fields.get("cache_write"), f"{at} cost.cache_write"),
        reasoning=_optional_money(fields.get("reasoning"), f"{at} cost.reasoning"),
        tiers=tuple(tiers),
        cache_write_long=_optional_money(
            fields.get("cache_write_long"), f"{at} cost.cache_write_long"
        ),
    )


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """`override` over `base`; the tables inside (limit, cost, modalities) merge key by key."""
    merged = dict(base)
    for key, value in override.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = {**cast("dict[str, Any]", current), **cast("dict[str, Any]", value)}
        else:
            merged[key] = value
    return merged


# -- reading values ------------------------------------------------------------------------------


def _read_user(text: str, where: str) -> dict[str, Any]:
    try:
        document = tomllib.loads(text, parse_float=Decimal)
    except tomllib.TOMLDecodeError as error:
        raise CatalogError(f"{where} is not valid TOML: {error}. Fix it or remove it.") from error
    _refuse_unknown(document, frozenset({"providers", "models"}), where)
    return document


def _refuse_unknown(fields: Mapping[str, object], known: frozenset[str], where: str) -> None:
    unknown = sorted(set(fields) - known)
    if unknown:
        raise CatalogError(
            f"{where} has unknown fields: {', '.join(unknown)}. Known: {', '.join(sorted(known))}."
        )


def _table(value: object, name: str, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CatalogError(f"{where}: {name} must be a table, got {value!r}.")
    return cast("dict[str, Any]", value)


def _list(value: object, name: str, where: str) -> list[Any]:
    if not isinstance(value, list):
        raise CatalogError(f"{where}: {name} must be a list, got {value!r}.")
    return cast("list[Any]", value)


def _text(value: object, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CatalogError(f"{where} must be non-empty text, got {value!r}.")
    return value.strip()


def _optional_text(value: object, where: str) -> str | None:
    return None if value is None else _text(value, where)


def _flag(value: object, where: str) -> bool:
    if not isinstance(value, bool):
        raise CatalogError(f"{where} must be true or false, got {value!r}.")
    return value


def _optional_flag(value: object, where: str) -> bool | None:
    return None if value is None else _flag(value, where)


def _choice(value: object, known: tuple[str, ...], where: str) -> str:
    if not isinstance(value, str) or value not in known:
        raise CatalogError(f"{where} is {value!r}; use one of: {', '.join(known)}.")
    return value


def _count(value: object, where: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CatalogError(f"{where} must be a whole number of tokens, got {value!r}.")
    return value


def _money(value: object, where: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, int | Decimal) or value < 0:
        raise CatalogError(f"{where} must be dollars per million tokens, got {value!r}.")
    return Decimal(value)


def _optional_money(value: object, where: str) -> Decimal | None:
    return None if value is None else _money(value, where)
