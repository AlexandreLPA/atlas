"""A stand-in for DeepSeek's harness (`dsh`), speaking ACP as `dsh --profile acp` does, for the
tests.

Atlas runs it as it runs `dsh` (`python tests/fake_dsh.py --profile acp [--patch file]`), so the
process, the pipes and the protocol are real; only the model behind it is a script, a JSON file
named by `FAKE_DSH_SCRIPT`: a list of steps for every prompt, or `{"prompts": {"words": [...]}}`,
the steps of the first entry whose words the prompt holds, and `Heard: <prompt>` for a prompt none
holds (a steer, a refusal's reason).

- `{"say": "text"}` and `{"think": "text"}`: the model's words, or its reasoning;
- `{"command": "ls"}`: a `bash` call, which asks (`session/request_permission`, `allow-once` or
  `reject-once`) unless its preset is `danger-full-access`;
- `{"write": "notes.txt", "content": "..."}`: a `write` call that asks the same way, and writes
  the file in the session's folder;
- `{"sleep": seconds}`, cut short when the prompt is cancelled;
- `{"limit": true}`: DeepSeek's 402 for a balance spent, which fails the prompt.

One prompt at a time, as `dsh` takes them: a second is refused. `session/cancel` ends the one that
runs (`cancelled`). Every line Atlas writes is appended to `FAKE_DSH_LOG`, after a first line with
the command line, its preset, its home, whether a key reached it, and the patch it was handed.

Its home is `DSH_HOME`, else `~/.dsh`: each session is a log of events in Zstandard frames, and a
projection in JSON beside it, as `dsh` writes them, so Atlas reads them from the files.
"""

import io
import json
import os
import queue
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import zstandard

VERSION = os.environ.get("FAKE_DSH_VERSION", "0.2.1-rc.2")
MODELS = [
    ("deepseek-v4-flash", '["deepseek-official","deepseek-v4-flash"]'),
    ("deepseek-v4-pro", '["deepseek-official","deepseek-v4-pro"]'),
]
EFFORTS = ["off", "low", "high", "max"]
NO_BALANCE = "turn failed: DeepSeek request failed (402): Insufficient Balance"


def home() -> Path:
    return Path(os.environ.get("DSH_HOME") or Path.home() / ".dsh")


def now() -> int:
    return int(time.time() * 1000)


class Stopped(Exception):  # noqa: N818 - the fake's own signal
    pass


class Session:
    """One session's files, as `dsh` keeps them."""

    def __init__(self, session: str, cwd: str) -> None:
        self.id = session
        self.cwd = cwd
        encoded = "--" + "".join(c if c.isalnum() else "-" for c in cwd) + "--"
        self.log = home() / "sessions" / encoded / session / "session.v4.jsonl.zstd"
        self.projection = home() / "storages" / "session_projcache" / "sessions" / f"{session}.json"
        self.events: list[dict[str, Any]] = []
        self.prompts = 0
        self.title = ""
        if self.log.is_file():
            raw = self.log.read_bytes()
            reader = zstandard.ZstdDecompressor().stream_reader(
                io.BytesIO(raw), read_across_frames=True
            )
            text = reader.read()
            self.events = [json.loads(line) for line in text.decode().splitlines() if line.strip()]
            loaded = json.loads(self.projection.read_text(encoding="utf-8"))
            rows = loaded["record"]["rows"]
            self.prompts = rows["titleInput"]["val"]["count"]
            self.title = rows["title"]["val"] or ""
        else:
            header = {
                "type": "session",
                "version": 4,
                "id": session,
                "createdAt": now(),
                "cwd": cwd,
            }
            self.events = [header]
            self.save()

    def add(self, kind: str, data: dict[str, Any]) -> None:
        self.events.append({"type": kind, "seq": len(self.events), "time": now(), "data": data})

    def save(self) -> None:
        self.log.parent.mkdir(parents=True, exist_ok=True)
        compressor = zstandard.ZstdCompressor()
        frames = b"".join(
            compressor.compress((json.dumps(event) + "\n").encode()) for event in self.events
        )
        self.log.write_bytes(frames)
        self.projection.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "identity": {
                "formatVersion": 4,
                "createdAt": self.events[0]["createdAt"],
                "cwd": self.cwd,
            },
            "rows": {
                "title": {"ver": 1, "seq": 0, "val": self.title or None},
                "titleInput": {"ver": 3, "seq": 0, "val": {"first": None, "count": self.prompts}},
            },
        }
        self.projection.write_text(json.dumps({"version": 7, "record": record}), encoding="utf-8")


class Agent:
    def __init__(self) -> None:
        self.out = threading.Lock()
        self.next_id = 1000
        self.replies: dict[int, queue.Queue[dict[str, Any]]] = {}
        self.sessions: dict[str, Session] = {}
        self.models: dict[str, str] = {}
        self.efforts: dict[str, str] = {}
        self.running: dict[str, threading.Event] = {}
        self.mode = os.environ.get("DSH_PERMISSION_MODE", "workspace-write")
        self.log_file = os.environ.get("FAKE_DSH_LOG")

    def log(self, entry: dict[str, Any]) -> None:
        if self.log_file:
            with Path(self.log_file).open("a", encoding="utf-8") as file:
                file.write(json.dumps(entry) + "\n")

    def send(self, message: dict[str, Any]) -> None:
        with self.out:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
            sys.stdout.flush()

    def update(self, session: str, update: dict[str, Any]) -> None:
        self.send({"method": "session/update", "params": {"sessionId": session, "update": update}})

    def ask(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.next_id += 1
        waiting: queue.Queue[dict[str, Any]] = queue.Queue()
        self.replies[self.next_id] = waiting
        self.send({"id": self.next_id, "method": method, "params": params})
        return waiting.get().get("result") or {}

    def handle(self, message: dict[str, Any]) -> None:
        if "method" not in message:
            waiting = self.replies.pop(int(message.get("id", -1)), None)
            if waiting is not None:
                waiting.put(message)
            return
        method = message["method"]
        params = message.get("params") or {}
        if "id" not in message:
            if method == "session/cancel" and params.get("sessionId") in self.running:
                self.running[params["sessionId"]].set()
            return
        if method == "session/prompt":
            if params.get("sessionId") in self.running:
                self.send(
                    {
                        "id": message["id"],
                        "error": {
                            "code": -32602,
                            "message": "a prompt is already in flight for this session",
                        },
                    }
                )
                return
            stop = self.running[params["sessionId"]] = threading.Event()
            threading.Thread(
                target=self.prompt, args=(message["id"], params, stop), daemon=True
            ).start()
            return
        handler = getattr(self, "on_" + method.replace("/", "_"), None)
        if handler is None:
            self.send({"id": message["id"], "error": {"code": -32601, "message": f"no {method}"}})
            return
        try:
            result = handler(params)
        except LookupError as error:
            self.send({"id": message["id"], "error": {"code": -32602, "message": str(error)}})
            return
        self.send({"id": message["id"], "result": result})

    def on_initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "protocolVersion": 1,
            "agentInfo": {"name": "deepseek-harness-acp", "version": "0.0.1"},
            "agentCapabilities": {
                "mcpCapabilities": {"http": True},
                "promptCapabilities": {"image": False},
                "sessionCapabilities": {"close": {}, "list": {}, "resume": {}},
            },
            "authMethods": [],
        }

    def options(self, session: str) -> list[dict[str, Any]]:
        model = self.models.get(session, MODELS[0][1])
        return [
            {
                "id": "model",
                "category": "model",
                "type": "select",
                "currentValue": model,
                "options": [
                    {
                        "group": "deepseek-official",
                        "name": "DeepSeek",
                        "options": [{"value": v, "name": n} for n, v in MODELS],
                    }
                ],
            },
            {
                "id": "reasoning_effort",
                "category": "thought_level",
                "type": "select",
                "currentValue": self.efforts.get(session, "high"),
                "options": [{"value": effort, "name": effort.title()} for effort in EFFORTS],
            },
        ]

    def on_session_new(self, params: dict[str, Any]) -> dict[str, Any]:
        session = str(uuid.uuid4())
        self.sessions[session] = Session(session, params["cwd"])
        return {"sessionId": session, "configOptions": self.options(session)}

    def on_session_resume(self, params: dict[str, Any]) -> dict[str, Any]:
        session = params["sessionId"]
        found = next(home().glob(f"sessions/*/{session}/session.v4.jsonl.zstd"), None)
        if found is None:
            raise LookupError(f"session not found: {session}")
        self.sessions[session] = Session(session, params["cwd"])
        return {"configOptions": self.options(session)}

    def on_session_close(self, params: dict[str, Any]) -> dict[str, Any]:
        self.sessions.pop(params["sessionId"], None)
        return {}

    def on_session_set_config_option(self, params: dict[str, Any]) -> dict[str, Any]:
        session, value = params["sessionId"], params["value"]
        if params["configId"] == "model":
            if value not in [v for _, v in MODELS]:
                raise LookupError(f"unknown model {value}")
            self.models[session] = value
        else:
            self.efforts[session] = value
        return {"configOptions": self.options(session)}

    def steps(self, prompt: str) -> list[dict[str, Any]]:
        script = os.environ.get("FAKE_DSH_SCRIPT")
        found: Any = json.loads(Path(script).read_text(encoding="utf-8")) if script else []
        if isinstance(found, dict):
            found = next(
                (v for k, v in found["prompts"].items() if k in prompt),
                [{"say": f"Heard: {prompt}"}],
            )
        return list(found)

    def prompt(self, request_id: int, params: dict[str, Any], stop: threading.Event) -> None:
        session_id = params["sessionId"]
        session = self.sessions[session_id]
        said = "".join(
            block.get("text", "")
            for block in params.get("prompt") or []
            if block.get("type") == "text"
        )
        session.prompts += 1
        session.title = session.title or said.split("\n", 1)[0][:40]
        session.add(
            "user/message",
            {
                "role": "user",
                "content": [{"type": "text", "text": said}],
                "source": {"kind": "user"},
            },
        )
        outcome = "end_turn"
        try:
            for step in self.steps(said):
                if stop.is_set():
                    raise Stopped
                if "say" in step:
                    self.update(
                        session_id,
                        {
                            "sessionUpdate": "agent_message_chunk",
                            "content": {"type": "text", "text": step["say"]},
                        },
                    )
                    session.add(
                        "assistant/message",
                        {
                            "message": {
                                "role": "assistant",
                                "content": [{"type": "text", "text": step["say"]}],
                            }
                        },
                    )
                elif "think" in step:
                    self.update(
                        session_id,
                        {
                            "sessionUpdate": "agent_thought_chunk",
                            "content": {"type": "text", "text": step["think"]},
                        },
                    )
                elif "command" in step:
                    self.tool(session, "bash", {"command": step["command"]}, None)
                elif "write" in step:
                    path = str(Path(session.cwd) / step["write"])
                    self.tool(
                        session,
                        "write",
                        {"file_path": path, "content": step.get("content", "")},
                        path,
                    )
                elif "sleep" in step:
                    stop.wait(step["sleep"])
                elif "limit" in step:
                    session.save()
                    self.send({"id": request_id, "error": {"code": -32603, "message": NO_BALANCE}})
                    return
            if stop.is_set():
                raise Stopped
        except Stopped:
            outcome = "cancelled"
        finally:
            self.running.pop(session_id, None)
        session.save()
        self.send({"id": request_id, "result": {"stopReason": outcome}})

    def tool(
        self, session: Session, name: str, arguments: dict[str, Any], path: str | None
    ) -> None:
        call = f"call-{uuid.uuid4().hex[:8]}"
        session.add("tool/call", {"callId": call, "name": name, "arguments": json.dumps(arguments)})
        self.update(
            session.id,
            {
                "sessionUpdate": "tool_call",
                "toolCallId": call,
                "title": name,
                "kind": "other",
                "status": "in_progress",
                "rawInput": arguments,
            },
        )
        allowed = True
        if self.mode != "danger-full-access":
            said = self.ask(
                "session/request_permission",
                {
                    "sessionId": session.id,
                    "toolCall": {
                        "toolCallId": call,
                        "title": name,
                        "kind": "other",
                        "rawInput": arguments,
                    },
                    "options": [
                        {"optionId": "allow-once", "name": "Allow once", "kind": "allow_once"},
                        {"optionId": "reject-once", "name": "Reject", "kind": "reject_once"},
                    ],
                },
            )
            outcome = said.get("outcome") or {}
            allowed = outcome.get("optionId") == "allow-once"
        if allowed and path:
            Path(path).write_text(arguments.get("content", ""), encoding="utf-8")
        output = "done" if allowed else "The user rejected this tool call."
        session.add(
            "tool/result",
            {
                "message": {
                    "role": "tool",
                    "toolCallId": call,
                    "content": [{"type": "text", "text": output}],
                    "isError": not allowed,
                }
            },
        )
        self.update(
            session.id,
            {
                "sessionUpdate": "tool_call_update",
                "toolCallId": call,
                "status": "completed" if allowed else "failed",
                "content": [{"type": "content", "content": {"type": "text", "text": output}}],
            },
        )


def main() -> None:
    arguments = sys.argv[1:]
    if arguments == ["--version"]:
        sys.stdout.write(VERSION + "\n")
        return
    if arguments[:2] != ["--profile", "acp"]:
        sys.stderr.write(f"unknown command {arguments}" + "\n")
        sys.exit(2)
    patch = ""
    if "--patch" in arguments:
        patch = Path(arguments[arguments.index("--patch") + 1]).read_text(encoding="utf-8")
    agent = Agent()
    agent.log(
        {
            "argv": arguments,
            "mode": agent.mode,
            "home": os.environ.get("DSH_HOME", ""),
            "keyed": bool(os.environ.get("DEEPSEEK_API_KEY")),
            "patch": patch,
        }
    )
    for line in sys.stdin:
        if not line.strip():
            continue
        message = json.loads(line)
        agent.log(message)
        agent.handle(message)


if __name__ == "__main__":
    main()
