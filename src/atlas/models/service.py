"""The interface the agent loop calls: a `ModelClient` for one provider and model, which streams a
`ModelRequest` into deltas and one `ModelReply`.

**The key comes from the vault, never from an environment variable** read on the side. Atlas V3
copied the vault into the process environment and read each key with `os.getenv`, so every child
process it started inherited every key.

**Checks that cost nothing run first** (`check_request`): a request the provider is known to refuse
is refused here, with the fix, before a round trip.

**Every call that goes out lands in the call ledger**, when one is given: finished, failed, or
stopped by the caller, with its usage and cost when they are known.
"""

import asyncio
import logging
import time
from collections.abc import AsyncGenerator
from contextlib import aclosing
from dataclasses import replace

from atlas.home import ModelSettings, format_utc, utc_now
from atlas.models.catalog import Catalog
from atlas.models.chat import ChatReader, chat_request
from atlas.models.errors import (
    ModelAuthError,
    ModelChoiceError,
    ModelKeyError,
    ModelOutOfCreditError,
    ModelRateLimitError,
    ModelRequestError,
    ModelsError,
    ModelUnavailableError,
)
from atlas.models.messages import MessagesReader, messages_request
from atlas.models.model import (
    AssistantMessage,
    CallOutcome,
    CallRecord,
    KeyCheck,
    KeyStatus,
    ModelChoice,
    ModelEvent,
    ModelInfo,
    ModelReply,
    ModelRequest,
    OfferedModel,
    OfferedProvider,
    Origin,
    Provider,
    ReplyDone,
    Wire,
    WireResult,
    check_choice,
    check_request,
    default_effort,
    finish_from,
    price_call,
)
from atlas.models.responses import ResponsesReader, responses_request
from atlas.models.store import CallLedger, Connection
from atlas.secrets import SecretsError, Vault

DEFAULT_RETRIES = 2
#: Seconds without a single byte from the provider before a call is given up, before the reply
#: starts or in the middle of it. Decided 07/10/2026, after Gemini held a reply open for 10 minutes
#: (the SDK's default) and sent nothing. A model that thinks in silence for longer is cut.
SILENCE_LIMIT = 120.0
#: Seconds a key check on the key screen waits for the provider.
KEY_CHECK_SILENCE = 20.0

#: How a call ended, by the kind of error that ended it.
_OUTCOMES: tuple[tuple[type[ModelsError], CallOutcome], ...] = (
    (ModelAuthError, CallOutcome.AUTH),
    (ModelOutOfCreditError, CallOutcome.OUT_OF_CREDIT),
    (ModelRateLimitError, CallOutcome.RATE_LIMITED),
    (ModelRequestError, CallOutcome.REFUSED),
    (ModelUnavailableError, CallOutcome.UNAVAILABLE),
)

_logger = logging.getLogger("atlas.models")


class Models:
    """The model calls of one Atlas: the catalog, the vault's keys, a connection per provider."""

    def __init__(
        self,
        catalog: Catalog,
        vault: Vault,
        settings: ModelSettings,
        *,
        ledger: CallLedger | None = None,
        retries: int = DEFAULT_RETRIES,
        silence: float = SILENCE_LIMIT,
    ) -> None:
        self.catalog = catalog
        self._vault = vault
        self._settings = settings
        self._ledger = ledger
        self._retries = retries
        self._silence = silence
        self._connections: dict[tuple[str, str, str | None], Connection] = {}
        self._automatic: str | None = None

    def client(
        self, provider: str | None = None, model: str | None = None, *, effort: str | None = None
    ) -> "ModelClient":
        """A client for a provider and model; with neither named, the one the settings chose, or,
        when they choose none, the first provider with a key in the vault.

        The key is read from the vault now, so a key stored while Atlas runs is used at once.
        """
        if provider is None:
            choice = self.default()
        else:
            choice = self.resolved(
                ModelChoice(provider, model or self._default_model(provider), effort)
            )
        chosen = self.catalog.provider(choice.provider)
        connection = self._connection(chosen, self._key(chosen))
        info = self.catalog.model(chosen, choice.model)
        return ModelClient(chosen, choice.model, info, choice.effort, connection, self._ledger)

    def use(self, settings: ModelSettings) -> None:
        """`[model]` changed on the settings screen (step 15b): a conversation that picked
        nothing follows it from its next turn."""
        self._settings = settings

    def default(self) -> ModelChoice:
        """What a conversation that picked nothing uses: `[model]` in the settings or, when they
        name no provider, the first provider with a key in the vault, with its default model; the
        settings' effort, or Atlas's default one."""
        settings = self._settings
        if settings.provider is None:
            if settings.name is not None:
                raise ModelChoiceError(
                    "[model] name is set without [model] provider. Name its provider too, in "
                    "config.toml (or ATLAS_MODEL_PROVIDER)."
                )
            provider = self._first_with_key()
            return self.resolved(
                ModelChoice(provider, self._default_model(provider), settings.effort)
            )
        model = settings.name or self._default_model(settings.provider)
        return self.resolved(ModelChoice(settings.provider, model, settings.effort))

    def resolved(self, choice: ModelChoice) -> ModelChoice:
        """`choice` with the effort it runs on: its own, or, when it names none, Atlas's default
        for the model (`DEFAULT_EFFORT` onto its levels; none for a model the catalog does not
        know reasons with levels)."""
        if choice.effort is not None:
            return choice
        info = self.catalog.model(self.catalog.provider(choice.provider), choice.model)
        if info is None:
            return choice
        return replace(choice, effort=default_effort(info.reasoning, info.efforts))

    def offered(self) -> list[OfferedProvider]:
        """The picker's list: the providers with a key in the vault, in the catalog's order, each
        with its curated models and those the user's models.toml adds. A provider with no key, a
        server on the user's machine, is offered when the user declared its models."""
        try:
            stored = set(self._vault.names())
        except SecretsError as error:
            raise ModelKeyError(f"The vault cannot be read to list the models: {error}") from error
        offered: list[OfferedProvider] = []
        for provider in self.catalog.providers.values():
            ids = dict.fromkeys(
                (
                    *provider.models,
                    *(model for (owner, model) in self.catalog.added if owner == provider.id),
                )
            )
            if provider.key is None and not ids:
                continue
            if provider.key is not None and provider.key not in stored:
                continue
            if provider.default_model is not None and not ids:
                ids = {provider.default_model: None}
            models = tuple(self._offered(provider, model) for model in ids)
            offered.append(OfferedProvider(provider.id, provider.name, models))
        return offered

    def keyed(self) -> list[tuple[Provider, bool]]:
        """The providers that take a key, in the catalog's order, each with whether the vault
        holds it: the key screen's list."""
        try:
            stored = set(self._vault.names())
        except SecretsError as error:
            raise ModelKeyError(f"The vault cannot be read: {error}") from error
        return [
            (provider, provider.key in stored)
            for provider in self.catalog.providers.values()
            if provider.key is not None
        ]

    def check(self, choice: ModelChoice) -> None:
        """Refuse a pick that cannot run: an unknown provider, one whose key the vault lacks, or a
        model or effort the catalog knows it refuses."""
        provider = self.catalog.provider(choice.provider)
        if provider.key is not None:
            try:
                stored = provider.key in self._vault.names()
            except SecretsError as error:
                raise ModelKeyError(f"The vault cannot be read: {error}") from error
            if not stored:
                raise ModelKeyError(
                    f"The vault has no key for {provider.name}. Store it with `atlas secrets set "
                    f"{provider.key}`."
                )
        check_choice(choice, self.catalog.model(provider, choice.model))

    def _offered(self, provider: Provider, model: str) -> OfferedModel:
        info = self.catalog.model(provider, model)
        if info is None:
            return OfferedModel(model, model, None, None)
        return OfferedModel(model, info.name, info.reasoning, info.efforts)

    def _default_model(self, provider_id: str) -> str:
        provider = self.catalog.provider(provider_id)
        if provider.default_model is None:
            raise ModelChoiceError(
                f"{provider.name} has no default model. Name one in [model] name in config.toml "
                "(or ATLAS_MODEL_NAME)."
            )
        return provider.default_model

    async def check_key(self, provider: str) -> KeyCheck:
        """Whether the provider takes the key in the vault, asked where it costs nothing."""
        chosen = self.catalog.provider(provider)
        try:
            key = self._key(chosen)
        except ModelKeyError as error:
            return KeyCheck(chosen.id, KeyStatus.MISSING, str(error))
        return await self._connection(chosen, key).check_key()

    async def connect(self, provider: str, key: str) -> KeyCheck:
        """Store `key` for `provider` unless the provider refuses it: the key screen
        (docs/ARCHITECTURE.md, The installer).

        The key is checked where it costs nothing, on a connection of its own closed after, and
        only a refusal (HTTP 401, 403) keeps it out. A used-up credit, a rate limit, a provider out
        of reach or an answer Atlas cannot read store it, the check's words saying what was found:
        proxies, regional blocks and flaky providers refuse good keys. Never retried, and given up
        after `KEY_CHECK_SILENCE`."""
        chosen = self.catalog.provider(provider)
        if chosen.key is None:
            raise ModelKeyError(f"{chosen.name} takes no key.")
        key = key.strip()
        if not key:
            raise ModelKeyError(f"The key for {chosen.name} is empty.")
        connection = Connection(chosen, key, retries=0, silence=KEY_CHECK_SILENCE)
        try:
            found = await connection.check_key()
        finally:
            await connection.close()
        if found.status is not KeyStatus.REFUSED:
            try:
                await asyncio.to_thread(self._vault.set, chosen.key, key)
            except SecretsError as error:
                raise ModelKeyError(f"The key for {chosen.name} was not stored: {error}") from error
        return found

    def _first_with_key(self) -> str:
        """The first provider, in the catalog's order, whose key is in the vault. A provider with no
        key, a server on the user's machine, is never chosen this way."""
        try:
            stored = set(self._vault.names())
        except SecretsError as error:
            raise ModelKeyError(f"The vault cannot be read to choose a model: {error}") from error
        keyed = [p for p in self.catalog.providers.values() if p.key is not None]
        for provider in keyed:
            if provider.key in stored:
                if self._automatic != provider.id:
                    self._automatic = provider.id
                    _logger.info(
                        "No model is chosen in the settings, so %s is used: the first provider "
                        "with a key in the vault. [model] provider chooses another.",
                        provider.name,
                        extra={"provider": provider.id},
                    )
                return provider.id
        choose = (
            "set [model] provider in config.toml (or ATLAS_MODEL_PROVIDER) to one of: "
            f"{', '.join(sorted(self.catalog.providers))}."
        )
        names = [str(p.key) for p in keyed]
        if names:
            others = f" (or {', '.join(names[1:])})" if len(names) > 1 else ""
            choose = f"Store one with `atlas secrets set {names[0]}`{others}, or {choose}"
        else:
            choose = choose[0].upper() + choose[1:]
        raise ModelChoiceError(
            f"No model is chosen, and the vault holds no provider's key. {choose}"
        )

    def _connection(self, provider: Provider, key: str | None) -> Connection:
        identity = (provider.id, provider.base_url, key)
        connection = self._connections.get(identity)
        if connection is None:
            connection = Connection(provider, key, retries=self._retries, silence=self._silence)
            self._connections[identity] = connection
        return connection

    def _key(self, provider: Provider) -> str | None:
        if provider.key is None:
            return None
        try:
            key = self._vault.get(provider.key)
        except SecretsError as error:
            raise ModelKeyError(f"The key for {provider.name} cannot be read: {error}") from error
        if key is None:
            raise ModelKeyError(
                f"There is no key for {provider.name}: the vault has no {provider.key}. "
                f"Store it with `atlas secrets set {provider.key}`."
            )
        return key

    async def close(self) -> None:
        """Close every connection. The clients made before stop working."""
        for connection in self._connections.values():
            await connection.close()
        self._connections.clear()


class ModelClient:
    """Calls to one model of one provider."""

    def __init__(
        self,
        provider: Provider,
        model: str,
        info: ModelInfo | None,
        effort: str | None,
        connection: Connection,
        ledger: CallLedger | None = None,
    ) -> None:
        self.provider = provider
        self.model = model
        #: What the catalog knows about the model, or `None`.
        self.info = info
        #: The effort used when a request names none.
        self.effort = effort
        self._connection = connection
        self._ledger = ledger

    async def stream(self, request: ModelRequest) -> AsyncGenerator[ModelEvent]:
        """The reply as it arrives: text and reasoning deltas, then `ReplyDone` with the reply."""
        effort = request.effort if request.effort is not None else self.effort
        if not request.reasoning:
            efforts = None if self.info is None else self.info.efforts
            effort = "none" if efforts is not None and "none" in efforts else None
        check_request(self.provider, self.model, self.info, request, effort)
        started_at, started = format_utc(utc_now()), time.monotonic()
        outcome, reply = CallOutcome.ERROR, None
        try:
            # `aclosing`: when the caller stops reading (the user pressed stop), the provider's
            # stream closes at once, without waiting on the interpreter to finalize the generator.
            if self.provider.wire is Wire.CHAT:
                chat = ChatReader(self.provider.name)
                body = chat_request(self.provider, self.model, request, self.info, effort)
                async with aclosing(self._connection.chat(body)) as chunks:
                    async for chunk in chunks:
                        for event in chat.read(chunk):
                            yield event
                result = chat.result()
            elif self.provider.wire is Wire.MESSAGES:
                reader = MessagesReader(self.provider.name)
                call = messages_request(self.provider, self.model, request, self.info, effort)
                async with aclosing(self._connection.messages(call.body, call.betas)) as events:
                    async for item in events:
                        for event in reader.read(item):
                            yield event
                read = reader.result()
                # The reply keeps the fingerprint of what it answered, so a later request can tell
                # whether its reasoning still holds (atlas.models.messages).
                result = replace(
                    read, provider_fields={**read.provider_fields, "prefix": call.prefix}
                )
            else:
                responses = ResponsesReader(self.provider.name)
                body = responses_request(self.provider, self.model, request, self.info, effort)
                async with aclosing(self._connection.responses(body)) as items:
                    async for item in items:
                        for event in responses.read(item):
                            yield event
                result = responses.result()
            reply = self._reply(result)
            outcome = CallOutcome.OK
            yield ReplyDone(reply)
        except (GeneratorExit, asyncio.CancelledError):
            if reply is None:
                outcome = CallOutcome.CANCELLED
            raise
        except ModelsError as error:
            outcome = next(
                (found for kind, found in _OUTCOMES if isinstance(error, kind)), CallOutcome.ERROR
            )
            raise
        finally:
            self._record(request, started_at, started, outcome, reply)

    async def reply(self, request: ModelRequest) -> ModelReply:
        """The whole reply, once it has arrived."""
        async for event in self.stream(request):
            if isinstance(event, ReplyDone):
                return event.reply
        raise ModelUnavailableError(f"{self.provider.name} ended the call without a reply.")

    def _record(
        self,
        request: ModelRequest,
        started_at: str,
        started: float,
        outcome: CallOutcome,
        reply: ModelReply | None,
    ) -> None:
        duration_ms = round((time.monotonic() - started) * 1000)
        _logger.debug(
            "Model call ended: %s.",
            outcome.value,
            extra={
                "provider": self.provider.id,
                "model": self.model,
                "purpose": request.purpose,
                "duration_ms": duration_ms,
            },
        )
        if self._ledger is None:
            return
        self._ledger.record(
            CallRecord(
                started_at=started_at,
                purpose=request.purpose,
                scope=request.scope,
                provider=self.provider.id,
                model=self.model,
                served_model=None if reply is None else reply.served_model,
                outcome=outcome,
                duration_ms=duration_ms,
                usage=None if reply is None else reply.usage,
                cost=price_call(None, None, reported=None, free=self.provider.free)
                if reply is None
                else reply.cost,
            )
        )

    def _reply(self, result: WireResult) -> ModelReply:
        message = AssistantMessage(
            text=result.text,
            tool_calls=result.tool_calls,
            reasoning=result.reasoning,
            origin=Origin(self.provider.id, self.model),
            provider_fields=result.provider_fields,
        )
        price = None if self.info is None else self.info.price
        return ModelReply(
            message=message,
            finish=finish_from(result.stop_reason, has_tool_calls=bool(result.tool_calls)),
            usage=result.usage,
            cost=price_call(
                result.usage, price, reported=result.reported_cost, free=self.provider.free
            ),
            served_model=result.served_model,
        )
