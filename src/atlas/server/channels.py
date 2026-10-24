"""Channels: each plugin's way to talk to Atlas, run as one more client of the gateway.

**A channel is a client, as the app is** (docs/ARCHITECTURE.md, Plugins): its bridge is a
`Connection` attached to the conversations of its chats, so it gets their events and their
questions and approvals as the app does, and it answers them the way the app does. A turn started
from the app in a Telegram conversation answers in Telegram too, and a question goes to both,
the first answer taking it (`request.cancel` settles the other).

**What is Atlas's, the same for every channel:**

- who may talk: pairing (`atlas.plugins`), before anything else is read;
- the commands: `/start`, `/new` (a new conversation from the next message), `/stop`;
- which conversation a chat continues, kept in the database, listed in the app with the channel as
  its source;
- questions: one at a time, by buttons or a typed answer; a question with several parts asks each
  in turn. An approval offers what the app offers;
- who may press a button: someone the channel lets in, and only that is checked; a press on a
  question that no longer waits is told so and changes nothing;
- the files people send (SDK version 7): kept in the home's attachments/ and read by the model as
  a file dropped in the app is (step 16g), at one point for every channel;
- what an outside executor asks (its permissions and its questions): shown and answered as Atlas's
  own are, the first answer, in a chat or on the app's card, taking it and the rest settled
  (decided with the user on 17/10/2026). A delegation's goes to the chat whose conversation made
  it. A session the user started in the app belongs to no chat, so what it asks goes to the chat
  of everyone the channel lets in (asked for by the user on 18/10/2026, where it used to stay in
  the app);
- a request still open when a channel starts (it was off, or started again) reaches it then: an
  outside executor's waits with no deadline.
"""

import asyncio
import contextlib
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, cast

from atlas.assistant import Assistant
from atlas.conversations import AttachmentError
from atlas.models import Attachment, UserMessage
from atlas.plugins import Channel, FileChannel, Inbound, LoadedChannel, Plugins, Prompt
from atlas.server.connection import Connection

PAIRING_TEXT = (
    "Atlas does not know you yet. Your pairing code is {code}, valid for an hour. Ask the owner "
    "of this Atlas to approve it (in the app, Plugins), then send your message again."
)
FULL_TEXT = "Atlas cannot take more people right now. Try again in an hour."
WELCOME_TEXT = "Atlas here. Write what you need."
NEW_TEXT = "A new conversation starts with your next message."
STOPPED_TEXT = "Stopped."
#: A file a channel handed over that Atlas could not keep (too heavy for its kind).
NOT_ATTACHED_TEXT = "A file was not attached: {why}"
IDLE_TEXT = "Nothing is running."
#: A file a tool sent, in a channel that cannot send files: it waits in the app.
FILE_TEXT = "Atlas sent a file, {name}: open it in the Atlas app."
#: What the chat gets when the agent asks for a key through the secrets tool (step 16d): the
#: channel never collects the value, which goes only through the app's masked card.
SECRET_NOTICE = (
    "Atlas asked for the key {name} for {reason}. "  # noqa: S105 - the chat's prompt, not a secret
    "Paste it in the Atlas app, on the card that just opened. Do not paste it here."
)
#: What an outside executor asks, for a delegation (step 13), as the server names its requests.
EXECUTOR_APPROVAL, EXECUTOR_QUESTION = "executor.approval", "executor.question"
#: The requests answered by a choice among a few.
_APPROVALS = ("approval", EXECUTOR_APPROVAL)
#: The requests a typed message answers too.
_QUESTIONS = ("ask_user", EXECUTOR_QUESTION)
_EXECUTOR_LABELS = {"allow": "Allow", "deny": "Deny"}
_APPROVAL_LABELS = {
    "once": "Allow once",
    "conversation": "Allow in this conversation",
    "always": "Always allow",
    "deny": "Deny",
}
_SETTLED = {
    "resolved": "Answered.",
    "timeout": "Nobody answered in time.",
    "withdrawn": "No longer needed.",
}

_logger = logging.getLogger("atlas.server")


class GatewayPort(Protocol):
    """What a channel's bridge needs of the gateway."""

    @property
    def assistant(self) -> Assistant: ...

    def attach(self, connection: Connection) -> None: ...

    def detach(self, connection: Connection) -> None: ...

    def answer(self, request_id: str, result: dict[str, Any]) -> bool: ...

    def held(self, conversations: Iterable[str]) -> list[dict[str, Any]]: ...

    def plugins_changed(self) -> None: ...

    def conversations_changed(self, conversation_id: str, status: str = "") -> None: ...


@dataclass
class _Asking:
    """A question or an approval shown in a chat, until it is answered or settled."""

    request: str
    chat: str
    method: str
    #: Its own among what the bridge shows: the request's id, with the chat's place when the
    #: request is shown in several chats, so each chat's prompt is its own.
    key: str
    #: An approval's choices, as the protocol names them.
    values: list[str] = field(default_factory=list[str])
    #: A question's parts, and the answers so far.
    questions: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    answers: dict[str, str | list[str] | None] = field(
        default_factory=dict[str, str | list[str] | None]
    )

    @property
    def prompt(self) -> str:
        """The id of the part shown now."""
        if self.method in _APPROVALS:
            return self.key
        return f"{self.key}.{len(self.answers)}"


class _NoPeer:
    """A bridge sends nothing over a socket: its frames are read where they are put."""

    async def send(self, text: str) -> None:  # pragma: no cover - never called
        return None

    async def receive(self) -> str | None:  # pragma: no cover - never called
        return None

    async def close(self, code: int) -> None:  # pragma: no cover - never called
        return None


class Bridge(Connection):
    """One running channel, as a client of the gateway."""

    def __init__(
        self, gateway: GatewayPort, plugins: Plugins, loaded: LoadedChannel, channel: Channel
    ) -> None:
        super().__init__(_NoPeer())
        self.plugin = loaded.plugin
        self.name = loaded.name
        self.channel = channel
        self._gateway = gateway
        self._plugins = plugins
        self._frames: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        #: The chat of each conversation the channel holds.
        self._chats: dict[str, str] = plugins.store.chats(self.name)
        self.conversations.update(self._chats)
        self._asking: dict[str, _Asking] = {}
        self._failure: str | None = None

    # -- the gateway's side -----------------------------------------------------------------------

    def put(self, frame: dict[str, Any]) -> None:
        if not self.gone:
            self._frames.put_nowait(frame)

    async def run(self) -> str:
        """Run the channel and hand it the frames, until it stops; why it stopped."""
        worker = asyncio.create_task(self._work())
        try:
            await self.channel.run(self)
            return self._failure or "The channel stopped by itself."
        except asyncio.CancelledError:
            if self._failure is None:
                raise
            return self._failure
        except Exception as error:  # noqa: BLE001 - a plugin's own code: its failure is told
            return str(error) or type(error).__name__
        finally:
            self.gone = True
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    async def _work(self) -> None:
        while True:
            frame = await self._frames.get()
            try:
                await self._deliver(frame)
            except Exception:  # one frame's failure must not stop the channel, logged
                _logger.exception(
                    "A channel could not deliver a frame.", extra={"channel": self.name}
                )

    async def _deliver(self, frame: dict[str, Any]) -> None:
        method = frame.get("method")
        params = cast("dict[str, Any]", frame.get("params") or {})
        if method in ("ask_user", "approval") and "id" in frame:
            chat = self._chats.get(str(params.get("conversation_id")))
            if chat is not None:
                await self._ask(str(frame["id"]), str(method), chat, params)
            return
        if method in (EXECUTOR_APPROVAL, EXECUTOR_QUESTION) and "id" in frame:
            request = str(frame["id"])
            conversation = str(params.get("conversation_id") or "")
            if conversation:
                # A delegation's: in its chat, when its conversation is one of this channel's.
                chat = self._chats.get(conversation)
                chats = [] if chat is None else [chat]
            else:
                # A session the user started belongs to no chat: everyone let in is asked.
                chats = await asyncio.to_thread(self._plugins.reached, self.name)
            for place, chat in enumerate(chats):
                key = request if len(chats) == 1 else f"{request}-{place}"
                await self._ask_executor(request, str(method), chat, params, key)
            return
        if method == "secret" and "id" in frame:
            # The channel never collects a secret: the value goes only through the app's masked
            # card (step 16d, roadmap "Chave pedida na conversa, pelo Telegram, nunca"). The chat
            # gets a one-line notice so the user knows what Atlas asked for; a value typed here
            # anyway falls through as a plain message, which is Atlas's defense (nothing in the
            # core looks for a key shape in chat text).
            chat = self._chats.get(str(params.get("conversation_id")))
            if chat is not None:
                await self.channel.send(
                    chat,
                    SECRET_NOTICE.format(
                        name=str(params.get("name") or ""),
                        reason=str(params.get("reason") or ""),
                    ),
                )
            return
        if method != "event":
            return
        kind = params.get("type")
        payload = cast("dict[str, Any]", params.get("payload") or {})
        if kind == "request.cancel":
            # Every chat it was shown in, but the one whose answer took it, already settled.
            settled = str(payload.get("id"))
            said = _SETTLED.get(str(payload.get("reason")), "No longer needed.")
            for key in [key for key, asking in self._asking.items() if asking.request == settled]:
                asking = self._asking.pop(key)
                await self.channel.settle(asking.chat, asking.prompt, said)
            return
        chat = self._chats.get(str(params.get("conversation_id")))
        if chat is None:
            return
        if kind == "message.start":
            await self.channel.working(chat, True)
        elif kind == "message.interim":
            # Each interim text as a message of its own, when it comes; neither the reasoning nor
            # the tools' progress goes. A message clears the chat's typing notice, so it is sent
            # again at once: it holds until the final answer.
            text = str(payload.get("text") or "").strip()
            if text:
                await self.channel.send(chat, text)
                await self.channel.working(chat, True)
        elif kind == "message.complete":
            await self.channel.working(chat, False)
            await self._complete(chat, payload)
        elif kind == "message.file":
            await self._file(chat, payload)

    async def _file(self, chat: str, payload: dict[str, Any]) -> None:
        """A file a tool sent the user (SDK version 22): sent in the chat by a channel that sends
        files, named in a line by one that does not."""
        path, name = Path(str(payload.get("path") or "")), str(payload.get("name") or "")
        send_file = getattr(self.channel, "send_file", None)
        if callable(send_file) and path.is_file():
            await cast("FileChannel", self.channel).send_file(
                chat, path, str(payload.get("media_type") or "")
            )
        else:
            await self.channel.send(chat, FILE_TEXT.format(name=name))

    async def _complete(self, chat: str, payload: dict[str, Any]) -> None:
        status = payload.get("status")
        text = str(payload.get("text") or "").strip()
        if status == "interrupted":
            await self.channel.send(chat, STOPPED_TEXT)
        elif status == "failed":
            await self.channel.send(chat, f"The turn failed: {text or payload.get('error')}")
        elif text:
            await self.channel.send(chat, text)

    async def _ask(self, request: str, method: str, chat: str, params: dict[str, Any]) -> None:
        if method == "approval":
            values = ["once"]
            if not params.get("guardian_denied"):
                values += [
                    value
                    for value, allowed in (
                        ("conversation", params.get("allow_conversation")),
                        ("always", params.get("allow_always")),
                    )
                    if allowed
                ]
            asking = _Asking(request, chat, method, request, [*values, "deny"])
        else:
            questions = cast("list[dict[str, Any]]", params.get("questions") or [])
            if not questions:
                return
            asking = _Asking(request, chat, method, request, questions=questions)
        self._asking[request] = asking
        await self.channel.ask(chat, self._prompt(asking, params))

    async def _ask_executor(
        self, request: str, method: str, chat: str, params: dict[str, Any], key: str
    ) -> None:
        if method == EXECUTOR_APPROVAL:
            asking = _Asking(request, chat, method, key, list(_EXECUTOR_LABELS))
        else:
            rows = cast("list[dict[str, Any]]", params.get("questions") or [])
            if not rows:
                return
            questions = [_executor_question(_asker(params), row) for row in rows]
            asking = _Asking(request, chat, method, key, questions=questions)
        self._asking[key] = asking
        await self.channel.ask(chat, self._prompt(asking, params))

    def _prompt(self, asking: _Asking, params: dict[str, Any] | None = None) -> Prompt:
        if asking.method == EXECUTOR_APPROVAL:
            details = params or {}
            summary = str(details.get("summary") or "")
            text = f"{_asker(details)} asks to use {details.get('tool')}:\n{summary}"
            said = [str(details.get(key) or "").strip() for key in ("title", "description")]
            added = [line for line in said if line and line != summary]
            if added:
                text += "\n\n" + "\n".join(added)
            return Prompt(asking.prompt, text, tuple(_EXECUTOR_LABELS.values()))
        if asking.method == "approval":
            details = params or {}
            text = (
                f"Atlas asks to use {details.get('tool')}:\n{details.get('target')}\n\n"
                f"Why it asks: {details.get('reason')}"
            )
            if details.get("guardian_denied"):
                text += "\n\nThe guardian judged it dangerous."
            return Prompt(asking.prompt, text, tuple(_APPROVAL_LABELS[v] for v in asking.values))
        question = asking.questions[len(asking.answers)]
        choices = tuple(
            str(choice) for choice in cast("list[object]", question.get("choices") or [])
        )
        return Prompt(asking.prompt, str(question.get("text")), choices, typed=True)

    # -- the channel's side -----------------------------------------------------------------------

    async def receive(self, message: Inbound) -> None:
        plugins = self._plugins
        admission = await asyncio.to_thread(
            plugins.admit, self.name, message.user, message.name, message.chat
        )
        if not admission.allowed:
            if admission.code:
                await self.channel.send(message.chat, PAIRING_TEXT.format(code=admission.code))
            else:
                await self.channel.send(message.chat, FULL_TEXT)
            if admission.new:
                self._gateway.plugins_changed()
            return
        text = message.text.strip()
        command = text.split()[0].split("@")[0].lower() if text.startswith("/") else None
        if command == "/start":
            await self.channel.send(message.chat, WELCOME_TEXT)
            return
        if command == "/new":
            self._open(message.chat, new=True)
            await self.channel.send(message.chat, NEW_TEXT)
            return
        if command == "/stop":
            conversation_id = plugins.store.conversation(self.name, message.chat)
            assistant = self._gateway.assistant
            if conversation_id is None or not assistant.running(conversation_id):
                await self.channel.send(message.chat, IDLE_TEXT)
                return
            await assistant.conversation(conversation_id).interrupt()
            return
        typed = next(
            (a for a in self._asking.values() if a.chat == message.chat and a.method in _QUESTIONS),
            None,
        )
        if typed is not None and text and not message.files:
            await self._answered(typed, text)
            return
        attached = await self._attached(message)
        if not text and not attached:
            return
        conversation_id = self._open(message.chat)
        self._gateway.assistant.conversation(conversation_id).send(UserMessage(text, attached))
        self._gateway.conversations_changed(conversation_id)

    async def _attached(self, message: Inbound) -> tuple[Attachment, ...]:
        """The files of a message, kept in the home's attachments/ as the app's are (step 16g);
        one Atlas cannot keep is told in the chat, and the message goes on without it."""
        attachments = self._gateway.assistant.attachments
        kept: list[Attachment] = []
        for file in message.files:
            try:
                added = await asyncio.to_thread(
                    attachments.add, "", file.name, len(file.data), 0, file.data
                )
            except AttachmentError as error:
                await self.channel.send(message.chat, NOT_ATTACHED_TEXT.format(why=error))
                continue
            if added.attachment is not None:
                kept.append(added.attachment)
        return tuple(kept)

    def _open(self, chat: str, *, new: bool = False) -> str:
        """The conversation the chat continues, a new one when it has none or `new`."""
        store = self._plugins.store
        conversation_id = None if new else store.conversation(self.name, chat)
        if conversation_id is None:
            assistant = self._gateway.assistant
            conversation_id = assistant.conversations.create(source=self.name).id
            # A chat's new conversation starts in "Auto", as the app's does.
            assistant.set_auto_mode(conversation_id, True)
            store.bind(self.name, chat, conversation_id)
        self._chats = {c: k for c, k in self._chats.items() if k != chat}
        self._chats[conversation_id] = chat
        self.conversations.add(conversation_id)
        return conversation_id

    async def choose(self, prompt: str, choice: int, user: str) -> str | None:
        if not await asyncio.to_thread(self._plugins.allowed, self.name, user):
            return "Only people this Atlas lets in can answer."
        asking = next((a for a in self._asking.values() if a.prompt == prompt), None)
        if asking is None:
            return "This no longer waits for an answer."
        if asking.method in _APPROVALS:
            if not 0 <= choice < len(asking.values):
                return "That is not one of the choices."
            value = asking.values[choice]
            del self._asking[asking.key]
            executor = asking.method == EXECUTOR_APPROVAL
            result = {"allow": value == "allow"} if executor else {"choice": value}
            if not self._gateway.answer(asking.request, result):
                return "This no longer waits for an answer."
            label = (_EXECUTOR_LABELS if executor else _APPROVAL_LABELS)[value]
            await self.channel.settle(asking.chat, prompt, f"You chose: {label}.")
            return None
        question = asking.questions[len(asking.answers)]
        choices = [str(c) for c in cast("list[object]", question.get("choices") or [])]
        if not 0 <= choice < len(choices):
            return "That is not one of the choices."
        await self._answered(asking, choices[choice])
        return None

    async def _answered(self, asking: _Asking, answer: str) -> None:
        """One part of a question answered: the next part, or the answers go back."""
        shown = asking.prompt
        question = asking.questions[len(asking.answers)]
        asking.answers[str(question.get("id"))] = (
            [answer] if question.get("multi_select") else answer
        )
        await self.channel.settle(asking.chat, shown, f"You answered: {answer}")
        if len(asking.answers) < len(asking.questions):
            await self.channel.ask(asking.chat, self._prompt(asking))
            return
        del self._asking[asking.key]
        self._gateway.answer(asking.request, {"answers": asking.answers})

    def failed(self, reason: str) -> None:
        self._failure = reason


def _asker(params: dict[str, Any]) -> str:
    """Who asks, as a chat reads it: the executor, and its folder for a session of the user's,
    which no chat started (a delegation's chat knows what it delegated)."""
    title = str(params.get("executor_title") or params.get("executor") or "")
    folder = str(params.get("folder") or "")
    if params.get("conversation_id") or not folder:
        return title
    return f"{title} ({Path(folder).name or folder})"


def _executor_question(who: str, row: dict[str, Any]) -> dict[str, Any]:
    """An executor's question as the chat asks one, keyed by its text, as its answers go back."""
    question = str(row.get("question") or "")
    header = str(row.get("header") or "").strip()
    choices = cast("list[dict[str, Any]]", row.get("choices") or [])
    asks = f"{who} asks ({header})" if header else f"{who} asks"
    return {
        "id": question,
        "text": f"{asks}: {question}",
        "choices": [str(choice.get("label")) for choice in choices],
        "multi_select": bool(row.get("multi_select")),
    }


@dataclass
class _Running:
    bridge: Bridge
    task: "asyncio.Task[None]"


class ChannelRunner:
    """The channels of the plugins that are on, started and stopped as the user decides."""

    def __init__(self, gateway: GatewayPort, plugins: Plugins) -> None:
        self._gateway = gateway
        self._plugins = plugins
        self._running: dict[str, list[_Running]] = {}
        self._seen: dict[str, tuple[bool, str | None]] | None = None
        self._lock = asyncio.Lock()

    def running(self, plugin: str) -> bool:
        return bool(self._running.get(plugin))

    async def reconcile(self, restart: Iterable[str] = ()) -> None:
        """Stop the channels of plugins turned off (and of `restart`), and start those of plugins
        turned on."""
        async with self._lock:
            again = set(restart)
            views = {view.name: view for view in await asyncio.to_thread(self._plugins.all)}
            for plugin in list(self._running):
                view = views.get(plugin)
                if plugin in again or view is None or not view.running:
                    await self._stop(plugin)
            wanted = {
                name
                for name, view in views.items()
                if view.running
                and view.manifest is not None
                and view.manifest.channels
                and name not in self._running
            }
            if wanted:
                for loaded in await self._plugins.channels(only=wanted):
                    self._start(loaded)
            self._seen = _snapshot(self._plugins)

    async def check(self) -> None:
        """Reconcile when a plugin was turned on or off elsewhere (the terminal)."""
        if self._seen != await asyncio.to_thread(_snapshot, self._plugins):
            await self.reconcile()

    async def secret_stored(self, name: str) -> None:
        """A secret was just written: the plugins that declare it start again with the new
        value, so their channels pick it up (step 16d: otherwise a new key would be ignored until a
        restart). A plugin that still cannot start keeps its own problem."""
        views = await asyncio.to_thread(self._plugins.all)
        declaring = {view.name for view in views if name in view.secrets and view.running}
        if declaring:
            await self.reconcile(restart=declaring)

    def _start(self, loaded: LoadedChannel) -> None:
        try:
            channel = loaded.make()
        except Exception as error:  # noqa: BLE001 - a plugin's own code: its failure is told
            self._failed(loaded.plugin, f"{type(error).__name__}: {error}")
            return
        bridge = Bridge(self._gateway, self._plugins, loaded, channel)
        self._gateway.attach(bridge)
        # What waits already, asked before this channel ran; with nothing awaited between the
        # two, a request asked after the attach reaches it once, by the attach alone.
        for frame in self._gateway.held(bridge.conversations):
            bridge.put(frame)
        task = asyncio.create_task(self._run(bridge))
        self._running.setdefault(loaded.plugin, []).append(_Running(bridge, task))

    async def _run(self, bridge: Bridge) -> None:
        reason = await bridge.run()
        self._gateway.detach(bridge)
        self._running[bridge.plugin] = [
            r for r in self._running.get(bridge.plugin, []) if r.bridge is not bridge
        ]
        if not self._running[bridge.plugin]:
            del self._running[bridge.plugin]
        self._failed(bridge.plugin, f"{bridge.name}: {reason}")

    def _failed(self, plugin: str, reason: str) -> None:
        self._plugins.failed(plugin, reason)
        self._gateway.plugins_changed()

    async def _stop(self, plugin: str) -> None:
        for running in self._running.pop(plugin, []):
            running.task.cancel()
            running.bridge.gone = True
            self._gateway.detach(running.bridge)
            with contextlib.suppress(asyncio.CancelledError):
                await running.task

    async def close(self) -> None:
        async with self._lock:
            for plugin in list(self._running):
                await self._stop(plugin)


def _snapshot(plugins: Plugins) -> dict[str, tuple[bool, str | None]]:
    return {name: (state.enabled, state.consent) for name, state in plugins.store.states().items()}
