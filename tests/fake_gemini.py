"""A stand-in for the `gemini` program, speaking ACP as `gemini --acp` does, for the tests.

Atlas runs it as it runs the Gemini CLI (`python tests/fake_gemini.py --acp`), so the process, the
pipes and the protocol are real; only the model behind it is a script, a JSON file named by
`FAKE_GEMINI_SCRIPT`: a list of steps for every prompt, or `{"prompts": {"words": [...]}}`, the
steps of the first entry whose words the prompt holds, and `Heard: <prompt>` for a prompt none
holds (a steer, a refusal's reason).

- `{"say": "text"}` and `{"think": "text"}`: the model's words, in two chunks each, or a thought;
- `{"command": "ls"}`: a `run_shell_command` call, which asks (`session/request_permission`)
  unless the session is in `yolo`; "done" when allowed, failed "canceled by the user" when not;
- `{"write": "notes.txt", "content": "..."}`: a `write_file` call that asks the same way, and
  writes the file in the session's folder;
- `{"sleep": seconds}`, cut short when the turn is cancelled or a new prompt comes;
- `{"limit": true}`: Google's 429 for a daily quota spent, which fails the prompt.

A second `session/prompt` ends the one that runs (`cancelled`) and goes on, as the Gemini CLI does;
`session/cancel` ends it too. With `FAKE_GEMINI_UNTRUSTED` set, the folder is not trusted and
`yolo` is refused. Every line Atlas writes is appended to `FAKE_GEMINI_LOG`, after a first line
with the command line and its `GEMINI_CLI_HOME`.

Its home is `GEMINI_CLI_HOME`, else the home folder, with its `.gemini` inside: it registers a
folder in `projects.json` and writes each session as the Gemini CLI does, so Atlas reads them from
the files. Logged in when `oauth_creds.json` is there or `GEMINI_API_KEY` is set; `authenticate`
logs Google's `other@example.com` in, or, with `FAKE_GEMINI_NO_PERSONAL_LOGIN` set, answers as
Google answers a personal account now.
"""

import json
import os
import queue
import re
import sys
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

VERSION = os.environ.get("FAKE_GEMINI_VERSION", "0.65.0")
MODELS = [
    {"modelId": "auto", "name": "Auto", "description": "Lets Gemini choose."},
    {"modelId": "gemini-2.5-pro", "name": "gemini-2.5-pro"},
    {"modelId": "gemini-3.5-flash", "name": "gemini-3.5-flash"},
]
COMMANDS = [
    {"name": "memory", "description": "Manage memory.", "input": {"hint": "show|refresh"}},
    {"name": "init", "description": "Analyze the project and write GEMINI.md."},
]
NO_PERSONAL_LOGIN = (
    "This client is no longer supported for Gemini Code Assist for individuals. To continue "
    "using Gemini, please migrate to the Antigravity suite of products: https://antigravity.google"
)
QUOTA = (
    "You have exhausted your daily quota on this model. Your quota will reset after 9h. "
    "Upgrade for higher limits."
)


def state() -> Path:
    return Path(os.environ.get("GEMINI_CLI_HOME") or Path.home()) / ".gemini"


def now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def read(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def key(folder: str) -> str:
    resolved = str(Path(folder).absolute())
    return resolved.lower() if sys.platform == "win32" else resolved


def project(folder: str) -> Path:
    """The folder's project, registered as the Gemini CLI registers it."""
    registry_file = state() / "projects.json"
    registry = read(registry_file).get("projects") or {}
    slug = registry.get(key(folder))
    if slug is None:
        base = re.sub(r"[^a-z0-9]+", "-", Path(folder).name.lower()).strip("-") or "project"
        taken = set(registry.values())
        slug, number = base, 0
        while slug in taken:
            number += 1
            slug = f"{base}-{number}"
        registry[key(folder)] = slug
        registry_file.parent.mkdir(parents=True, exist_ok=True)
        registry_file.write_text(json.dumps({"projects": registry}), encoding="utf-8")
    place = state() / "tmp" / slug
    place.mkdir(parents=True, exist_ok=True)
    (place / ".project_root").write_text(key(folder), encoding="utf-8")
    return place


def logged_in() -> bool:
    return (state() / "oauth_creds.json").is_file() or bool(os.environ.get("GEMINI_API_KEY"))


class AbortedError(Exception):
    pass


class Agent:
    def __init__(self) -> None:
        self.out = threading.Lock()
        self.next_id = 1000
        self.replies: dict[int, queue.Queue[dict[str, Any]]] = {}
        self.session = ""
        self.cwd = str(Path.cwd())
        self.file: Path | None = None
        self.mode = "default"
        self.model = "auto"
        self.running: threading.Event | None = None
        self.log_file = os.environ.get("FAKE_GEMINI_LOG")

    # -- the wire ---------------------------------------------------------------------------------

    def log(self, entry: dict[str, Any]) -> None:
        if self.log_file:
            with Path(self.log_file).open("a", encoding="utf-8") as file:
                file.write(json.dumps(entry) + "\n")

    def send(self, message: dict[str, Any]) -> None:
        with self.out:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
            sys.stdout.flush()

    def update(self, update: dict[str, Any]) -> None:
        self.send(
            {"method": "session/update", "params": {"sessionId": self.session, "update": update}}
        )

    def ask(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.next_id += 1
        request_id = self.next_id
        waiting: queue.Queue[dict[str, Any]] = queue.Queue()
        self.replies[request_id] = waiting
        self.send({"id": request_id, "method": method, "params": params})
        return waiting.get().get("result") or {}

    def later(self, work: Any) -> None:
        threading.Timer(0.05, work).start()

    # -- requests ---------------------------------------------------------------------------------

    def handle(self, message: dict[str, Any]) -> None:
        if "method" not in message:
            waiting = self.replies.pop(int(message.get("id", -1)), None)
            if waiting is not None:
                waiting.put(message)
            return
        method = message["method"]
        params = message.get("params") or {}
        if "id" not in message:
            if method == "session/cancel" and self.running is not None:
                self.running.set()
            return
        if method == "session/prompt":
            threading.Thread(target=self.prompt, args=(message["id"], params), daemon=True).start()
            return
        name = "on_" + method.replace("/", "_")
        handler = getattr(self, name, None)
        if handler is None:
            self.send({"id": message["id"], "error": {"code": -32601, "message": f"no {method}"}})
            return
        try:
            result = handler(params)
        except PermissionError as error:
            self.send({"id": message["id"], "error": {"code": -32000, "message": str(error)}})
            return
        except LookupError as error:
            self.send({"id": message["id"], "error": {"code": -32603, "message": str(error)}})
            return
        self.send({"id": message["id"], "result": result})

    def on_initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "protocolVersion": 1,
            "agentInfo": {"name": "gemini-cli", "title": "Gemini CLI", "version": VERSION},
            "agentCapabilities": {"loadSession": True},
            "authMethods": [
                {"id": "oauth-personal", "name": "Log in with Google"},
                {"id": "gemini-api-key", "name": "Gemini API key"},
            ],
        }

    def on_authenticate(self, params: dict[str, Any]) -> dict[str, Any]:
        if os.environ.get("FAKE_GEMINI_NO_PERSONAL_LOGIN"):
            raise PermissionError(NO_PERSONAL_LOGIN)
        home = state()
        home.mkdir(parents=True, exist_ok=True)
        settings = read(home / "settings.json")
        settings.setdefault("security", {}).setdefault("auth", {})["selectedType"] = params[
            "methodId"
        ]
        (home / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
        (home / "google_accounts.json").write_text(
            json.dumps({"active": "other@example.com", "old": []}), encoding="utf-8"
        )
        expiry = int((time.time() + 3600) * 1000)
        (home / "oauth_creds.json").write_text(
            json.dumps({"access_token": "fresh-token", "expiry_date": expiry}), encoding="utf-8"
        )
        return {}

    def opened(self) -> dict[str, Any]:
        self.later(
            lambda: self.update(
                {"sessionUpdate": "available_commands_update", "availableCommands": COMMANDS}
            )
        )
        return {
            "modes": {
                "availableModes": [{"id": "default"}, {"id": "autoEdit"}, {"id": "yolo"}],
                "currentModeId": self.mode,
            },
            "models": {"availableModels": MODELS, "currentModelId": self.model},
        }

    def on_session_new(self, params: dict[str, Any]) -> dict[str, Any]:
        if not logged_in():
            raise PermissionError("Gemini API key is missing or not configured.")
        self.cwd = params["cwd"]
        self.session = str(uuid.uuid4())
        chats = project(self.cwd) / "chats"
        chats.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M")
        self.file = chats / f"session-{stamp}-{self.session[:8]}.jsonl"
        self.append(
            {
                "sessionId": self.session,
                "projectHash": "fake",
                "startTime": now(),
                "lastUpdated": now(),
                "kind": "main",
            }
        )
        return {"sessionId": self.session, **self.opened()}

    def on_session_load(self, params: dict[str, Any]) -> dict[str, Any]:
        if not logged_in():
            raise PermissionError("Authentication required.")
        self.cwd = params["cwd"]
        wanted = params["sessionId"]
        chats = project(self.cwd) / "chats"
        found = None
        for file in chats.glob(f"session-*-{wanted[:8]}.jsonl"):
            first = json.loads(file.read_text(encoding="utf-8").splitlines()[0])
            if first.get("sessionId") == wanted:
                found = file
        if found is None:
            raise LookupError(f"No previous sessions found for this project: {wanted}")
        self.session, self.file = wanted, found
        messages = [
            json.loads(line)
            for line in found.read_text(encoding="utf-8").splitlines()
            if '"type"' in line
        ]

        def replay() -> None:
            for message in messages:
                kind = "user_message_chunk" if message["type"] == "user" else "agent_message_chunk"
                text = message["content"]
                if isinstance(text, list):
                    text = "".join(part.get("text", "") for part in text)
                self.update({"sessionUpdate": kind, "content": {"type": "text", "text": text}})

        self.later(replay)
        return self.opened()

    def on_session_set_mode(self, params: dict[str, Any]) -> dict[str, Any]:
        if os.environ.get("FAKE_GEMINI_UNTRUSTED") and params["modeId"] in ("yolo", "autoEdit"):
            raise LookupError("Cannot enable privileged approval modes in an untrusted folder.")
        self.mode = params["modeId"]
        return {}

    def on_session_set_model(self, params: dict[str, Any]) -> dict[str, Any]:
        self.model = params["modelId"]
        return {}

    # -- a turn -----------------------------------------------------------------------------------

    def steps(self, prompt: str) -> list[dict[str, Any]]:
        script = os.environ.get("FAKE_GEMINI_SCRIPT")
        found: Any = json.loads(Path(script).read_text(encoding="utf-8")) if script else []
        if isinstance(found, dict):
            found = next(
                (v for k, v in found["prompts"].items() if k in prompt),
                [{"say": f"Heard: {prompt}"}],
            )
        return list(found)

    def prompt(self, request_id: int, params: dict[str, Any]) -> None:
        if self.running is not None:
            self.running.set()
        stop = self.running = threading.Event()
        blocks = params.get("prompt") or []
        said = " ".join(block.get("text", "") for block in blocks if block.get("type") == "text")
        self.append(
            {
                "id": str(uuid.uuid4()),
                "timestamp": now(),
                "type": "user",
                "content": [{"text": said}],
            }
        )
        words: list[str] = []
        calls: list[dict[str, Any]] = []
        try:
            for step in self.steps(said):
                if stop.is_set():
                    raise AbortedError
                if "say" in step:
                    text = step["say"]
                    for chunk in (text[: len(text) // 2], text[len(text) // 2 :]):
                        self.update(
                            {
                                "sessionUpdate": "agent_message_chunk",
                                "content": {"type": "text", "text": chunk},
                            }
                        )
                    words.append(text)
                elif "think" in step:
                    self.update(
                        {
                            "sessionUpdate": "agent_thought_chunk",
                            "content": {"type": "text", "text": step["think"]},
                        }
                    )
                elif "command" in step:
                    calls.append(self.tool("run_shell_command", "execute", step["command"], {}))
                elif "write" in step:
                    path = str(Path(self.cwd) / step["write"])
                    calls.append(
                        self.tool(
                            "write_file",
                            "edit",
                            f"Writing to {step['write']}",
                            {"file_path": path, "content": step.get("content", "")},
                        )
                    )
                elif "sleep" in step:
                    stop.wait(step["sleep"])
                elif "limit" in step:
                    self.send({"id": request_id, "error": {"code": 429, "message": QUOTA}})
                    return
            if stop.is_set():
                raise AbortedError
        except AbortedError:
            self.record(words, calls)
            self.send({"id": request_id, "result": {"stopReason": "cancelled"}})
            return
        finally:
            if self.running is stop:
                self.running = None
        self.record(words, calls)
        usage = {"inputTokens": 10, "outputTokens": 5, "totalTokens": 15}
        self.send({"id": request_id, "result": {"stopReason": "end_turn", "usage": usage}})

    def tool(self, name: str, kind: str, title: str, args: dict[str, Any]) -> dict[str, Any]:
        call = f"{name}-{int(time.time() * 1000)}"
        path = args.get("file_path")
        locations = [{"path": path}] if path else []
        # As the Gemini CLI sends it: a write's call carries its diff, a list of blocks.
        content = (
            [{"type": "diff", "path": path, "oldText": "", "newText": args.get("content", "")}]
            if path
            else []
        )
        self.update(
            {
                "sessionUpdate": "tool_call",
                "toolCallId": call,
                "status": "pending",
                "title": title,
                "kind": kind,
                "locations": locations,
                "content": content,
            }
        )
        allowed = True
        if self.mode != "yolo":
            said = self.ask(
                "session/request_permission",
                {
                    "sessionId": self.session,
                    "toolCall": {
                        "toolCallId": call,
                        "status": "pending",
                        "title": title,
                        "kind": kind,
                        "locations": locations,
                        "content": content,
                    },
                    "options": [
                        {
                            "optionId": "proceed_always",
                            "name": "Allow for this session",
                            "kind": "allow_always",
                        },
                        {"optionId": "proceed_once", "name": "Allow", "kind": "allow_once"},
                        {"optionId": "cancel", "name": "Reject", "kind": "reject_once"},
                    ],
                },
            )
            outcome = said.get("outcome") or {}
            allowed = outcome.get("outcome") == "selected" and outcome.get("optionId") != "cancel"
        if allowed and path:
            Path(path).write_text(args.get("content", ""), encoding="utf-8")
        output = "done" if allowed else f'Tool "{name}" was canceled by the user.'
        self.update(
            {
                "sessionUpdate": "tool_call_update",
                "toolCallId": call,
                "status": "completed" if allowed else "failed",
                "content": [{"type": "content", "content": {"type": "text", "text": output}}],
            }
        )
        return {
            "id": call,
            "name": name,
            "args": {"command": title, **args} if kind == "execute" else args,
            "result": [
                {"functionResponse": {"id": call, "name": name, "response": {"output": output}}}
            ],
            "status": "success" if allowed else "cancelled",
            "timestamp": now(),
            "resultDisplay": output,
        }

    def record(self, words: list[str], calls: list[dict[str, Any]]) -> None:
        self.append(
            {
                "id": str(uuid.uuid4()),
                "timestamp": now(),
                "type": "gemini",
                "content": "\n".join(words),
                "toolCalls": calls,
                "model": "gemini-2.5-pro",
            }
        )
        self.append({"$set": {"lastUpdated": now()}})

    def append(self, record: dict[str, Any]) -> None:
        if self.file is not None:
            with self.file.open("a", encoding="utf-8") as file:
                file.write(json.dumps(record) + "\n")


def main() -> None:
    arguments = sys.argv[1:]
    if arguments == ["--version"]:
        sys.stdout.write(VERSION + "\n")
        return
    if arguments[:1] != ["--acp"]:
        sys.stderr.write(f"unknown command {arguments}" + "\n")
        sys.exit(2)
    agent = Agent()
    agent.log(
        {
            "argv": arguments,
            "home": os.environ.get("GEMINI_CLI_HOME", ""),
            "key": os.environ.get("GEMINI_API_KEY", ""),
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
