"""A stand-in for the `claude` program, speaking its stdio control protocol for the tests.

Atlas runs it as it runs Claude Code (`python tests/fake_claude.py <claude's own flags>`), so the
process, the pipes and the protocol are real; only the model behind it is a script. The script is
a JSON file named by `FAKE_CLAUDE_SCRIPT`, a list of steps, or a folder of them, one per project,
named after the folder Claude Code works in (`<folder>.json`):

- `{"say": "text"}` and `{"think": "text"}`: an assistant message; with `"late": seconds`, its line
  reaches the session's file only that long after it is said, as Claude Code writes its file
  behind what it streams;
- `{"tool": "Bash", "input": {...}}`: asks permission (`can_use_tool`), then returns the tool's
  result: "done" when allowed, the denial's message as an error when not; an allowed `Write`
  writes its `content` to its `file_path`;
- `{"questions": [...]}`: asks `AskUserQuestion`, and returns the answers it got;
- `{"sleep": seconds}`: says nothing for a while;
- `{"compact": [before, after]}`: compacts the session by itself, as Claude Code does near its
  window: a `compact_boundary`, and in the kept session a boundary with no parent and the
  summary after it;
- `{"result": "text"}`: ends the turn.

A script may instead be `{"turns": [[...], [...]]}`: one list of steps per prompt, in order, the
last again for any prompt after. In the `auto` permission mode (`--permission-mode auto`, or
`set_permission_mode`) a tool runs without asking, as Claude Code's does, apart from a `Bash`
command an `ask` rule of the user's own settings names (`Bash(git push *)`, in the file
`FAKE_CLAUDE_USER_SETTINGS` names, standing in for `~/.claude/settings.json`); questions are asked
all the same.

Every line Atlas writes is appended to `FAKE_CLAUDE_LOG`, with the command line and the
environment's credential variables first, so a test can read what Claude Code received. An
`interrupt` ends the turn at once with `aborted_streaming`, as Claude Code does. A user message
that arrives while a turn runs is a steer: the turn takes it, and says `Steered: <text>`. After a
turn the process waits for the next prompt, which runs the script again. `--version` prints
`FAKE_CLAUDE_VERSION`, or 2.1.140.

With `FAKE_CLAUDE_PROJECTS` set, it keeps the session as Claude Code does (step 16f), one JSON
object a line in `<projects>/<the folder's name>/<session>.jsonl`, each line naming its parent, so
Atlas lists it and reads it back. `--resume=<id>` goes on with that session, in the same file. Its
`initialize` answers the slash commands it takes, and so does its `init`. With `--fork-session`
it goes on in a copy of the resumed session, under an id of its own.

Its `initialize` answers its models too (`MODELS`, in Claude Code 2.1.296's shape), and each
turn's `init` names the one it runs as: `--model`'s, or the last `set_model`'s, `default` when
none. `--effort` and `apply_flag_settings` are taken as Claude Code takes them, and logged.

Its `initialize` answers the login's `account`, on a claude.ai plan (`FAKE_CLAUDE_SUBSCRIPTION`,
or "Claude Max"; empty, an API key's login), and `mcp_status` its MCP servers as 2.1.296 does: a
user's, a plugin's and, on a plan, the account's claude.ai connectors, which come only from the
second `mcp_status` on, one of them still `pending` there and connected from the third.

`get_usage` answers the plan's windows as 2.1.296 does (`USAGE`): used 0 to 100, resets as ISO
8601, the code-named windows it does not show left null, and a per-model window; an API key's
login has none (`rate_limits_available` false), and `FAKE_CLAUDE_USAGE=refuse` answers it as a
Claude Code that does not know the request. A `{"usage": [session, week]}` step writes the
`rate_limit_event` 2.1.296 writes after each request, its `unifiedWindows` used 0 to 1.

Started with a `CLAUDE_CONFIG_DIR` named `.claude-<nickname>` (another account, step 16h), it
keeps that account in it, as Claude Code keeps each account in its own folder: its login
(`fake-auth.json`, nobody logged in until `auth login`, which takes `--email` or logs in as
`other@example.com`) and its sessions (`projects/`). `--resume=<path of a transcript>` with
`--fork-session` goes on in a copy of that file, kept in this folder under an id of its own, the
original left as it was. `FAKE_CLAUDE_LIMITED` names the accounts at their plan's limit (their
nicknames, `own` for the user's own folder), as a real one is: every prompt there stops at the
limit, an hour from now, and `get_usage` says its session window is used up. `{"account": true}`
says which folder it runs on (its name, or `own`); the log names the folder too. There,
`mcp_status` has only the account's claude.ai connectors.

Its `initialize` describes its slash commands as 2.1.296 does (`COMMAND_ROWS`: its own marked
`builtin`, a skill with its hint), with the skills of the folder's `.claude/skills/` after them. A
prompt that is one of the commands it runs itself (`LOCAL`: `/context`) answers with its words and
no model, a synthetic message and a result, and is kept as Claude Code keeps it: a user line of
tags and a `system` line of subtype `local_command` with the output. A `{"commands_changed":
[name, ...]}` step says the list again, as Claude Code does when it finds a skill.
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

from atlas.home import locked

SESSION = "fake-session-0001"
#: A session id per project instead, when set: `{folder}` is the folder Claude Code works in.
SESSION_TEMPLATE = os.environ.get("FAKE_CLAUDE_SESSION", "")


#: The slash commands it says it takes.
COMMANDS = ["compact", "review"]
#: How its `initialize` describes them (2.1.296's shape).
COMMAND_ROWS = [
    {"name": "compact", "description": "Clear the conversation history but keep a summary",
     "argumentHint": "<optional custom summarization instructions>", "builtin": True},
    {"name": "review", "description": "Review a pull request (user)", "argumentHint": "[pr]"},
]  # fmt: skip
#: The commands it runs itself, with no model, and what each says.
LOCAL = {"context": "## Context Usage\n\n**Tokens:** 20.1k / 1m (2%)"}


def _command_rows() -> list[dict[str, Any]]:
    """Its commands in the folder it works in: its own and the user's, then the project's
    skills (`.claude/skills/<name>/`)."""
    skills = Path.cwd() / ".claude" / "skills"
    found = (
        sorted(path.name for path in skills.iterdir() if path.is_dir()) if skills.is_dir() else []
    )
    return COMMAND_ROWS + [
        {"name": name, "description": f"The {name} skill (project)", "argumentHint": ""}
        for name in found
    ]


#: The models its `initialize` answers: its default, one with fewer efforts, one with none.
MODELS = [
    {"value": "default", "resolvedModel": "fake-opus", "displayName": "Default (recommended)",
     "description": "Fake Opus · Best for everyday tasks", "supportsEffort": True,
     "supportedEffortLevels": ["low", "medium", "high", "xhigh", "max"]},
    {"value": "sonnet", "resolvedModel": "fake-sonnet", "displayName": "Fake Sonnet",
     "description": "Most efficient for simpler tasks", "supportsEffort": True,
     "supportedEffortLevels": ["low", "medium", "high"]},
    {"value": "haiku", "resolvedModel": "fake-haiku", "displayName": "Fake Haiku",
     "description": "Fastest for quick answers"},
]  # fmt: skip


#: The folder of the other account it runs on (step 16h), as Claude Code names one; `None`, the
#: user's own, standing in by `FAKE_CLAUDE_AUTH` and `FAKE_CLAUDE_PROJECTS`.
_CONFIG = Path(os.environ.get("CLAUDE_CONFIG_DIR") or ".")
ACCOUNT = _CONFIG if _CONFIG.name.startswith(".claude-") else None
#: This account is at its plan's limit (`FAKE_CLAUDE_LIMITED`).
LIMITED = (ACCOUNT.name.removeprefix(".claude-") if ACCOUNT else "own") in os.environ.get(
    "FAKE_CLAUDE_LIMITED", ""
).split(",")
#: The login `initialize` answers: a plan's, unless the test says an API key's.
SUBSCRIPTION = os.environ.get("FAKE_CLAUDE_SUBSCRIPTION", "Claude Max")
#: Its MCP servers, as `mcp_status` names them, without the account's.
SERVERS = [
    {"name": "files", "status": "connected", "scope": "user", "config": {"type": "stdio"},
     "tools": [{"name": "read"}, {"name": "write"}]},
    {"name": "plugin:marketing:slack", "status": "needs-auth", "scope": "dynamic",
     "source": "plugin", "config": {"type": "http"}},
    {"name": "plugin:design:gmail", "status": "failed", "scope": "dynamic", "source": "plugin",
     "error": "No URL configured for this server", "config": {"type": "http"}},
]  # fmt: skip
if ACCOUNT is not None:
    # Another account's folder has none of the user's own servers and plugins: only its claude.ai
    # connectors.
    SERVERS = []


#: The tokens each reply read and wrote, as the session file keeps them.
USED = {"input_tokens": 1_200, "cache_read_input_tokens": 30_000, "output_tokens": 34}

#: The plan's windows `get_usage` answers, as 2.1.296 does (18/10/2026).
USAGE = {
    "five_hour": {"utilization": 45, "resets_at": "2026-10-18T19:10:00.378687+00:00",
                  "limit_dollars": None, "locked_reason": None},
    "seven_day": {"utilization": 25, "resets_at": "2026-10-23T08:00:00.378707+00:00",
                  "limit_dollars": None, "locked_reason": None},
    "seven_day_oauth_apps": None, "seven_day_opus": None, "seven_day_sonnet": None,
    "tangelo": None,
    "model_scoped": [{"display_name": "Fable", "utilization": 60,
                      "resets_at": "2026-10-23T08:00:00+00:00"}],
    "extra_usage": {"is_enabled": False, "monthly_limit": None, "used_credits": None,
                    "utilization": None, "currency": "BRL"},
}  # fmt: skip


def _usage() -> dict[str, Any]:
    """What `get_usage` answers with `skip_behaviors`: the plan's windows, none for an API key."""
    plan = SUBSCRIPTION.removeprefix("Claude ").lower() or None
    return {
        "session": {"total_cost_usd": 0, "model_usage": {}},
        "subscription_type": plan,
        "rate_limits_available": bool(SUBSCRIPTION),
        "rate_limits": (_used_up() if LIMITED else USAGE) if SUBSCRIPTION else None,
        "behaviors": None,
    }


def _used_up() -> dict[str, Any]:
    """The plan's windows of an account at its limit: its session window used up, for an hour."""
    resets = datetime.fromtimestamp(time.time() + 3600, UTC).isoformat()
    session = {**USAGE["five_hour"], "utilization": 100, "resets_at": resets}
    return {**USAGE, "five_hour": session}


def _account_servers(asked: int) -> list[dict[str, Any]]:
    """The claude.ai connectors, as the `asked`-th `mcp_status` finds them."""
    if not SUBSCRIPTION or asked < 2:
        return []
    drive = "connected" if asked >= 3 else "pending"
    return [
        {"name": "claude.ai Google Drive", "status": drive, "scope": "claudeai",
         "source": "claudeai", "tools": [{"name": "search_files"}] if drive == "connected" else []},
        {"name": "claude.ai Gmail", "status": "needs-auth", "scope": "claudeai",
         "source": "claudeai"},
    ]  # fmt: skip


def _runs_as(model: str | None) -> str:
    """The model a turn's `init` names for what was chosen."""
    found = next((m for m in MODELS if m["value"] == (model or "default")), None)
    return str(found["resolvedModel"]) if found else str(model)


#: The session `--resume` names, and a copy's own id under `--fork-session`; another account's
#: session is named by its file's path, whose name is its id.
_NAMED = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--resume=")), "")
_FROM = Path(_NAMED) if _NAMED.endswith(".jsonl") else None
_RESUMED = _FROM.stem if _FROM is not None else _NAMED
_FORK = str(uuid.uuid4()) if "--fork-session" in sys.argv else ""


def _session() -> str:
    if _FORK or _RESUMED:
        return _FORK or _RESUMED
    return SESSION_TEMPLATE.format(folder=Path.cwd().name) if SESSION_TEMPLATE else SESSION


class _Kept:
    """The session's file, as Claude Code writes it: each line its parent's child."""

    def __init__(self) -> None:
        projects = str(ACCOUNT / "projects") if ACCOUNT else os.environ.get("FAKE_CLAUDE_PROJECTS")
        folder = re.sub(r"[^a-zA-Z0-9]", "-", str(Path.cwd()))
        self.file = None if not projects else Path(projects) / folder / f"{_session()}.jsonl"
        self.parent: str | None = None
        if self.file is not None and _FORK and _RESUMED:
            # A copy starts with the original's lines, under its own id.
            original = _FROM or self.file.parent / f"{_RESUMED}.jsonl"
            if original.exists():
                text = original.read_text(encoding="utf-8").replace(_RESUMED, _FORK)
                self.file.parent.mkdir(parents=True, exist_ok=True)
                self.file.write_text(text, encoding="utf-8")
        if self.file is not None and self.file.exists():
            # Going on: the next line hangs from the last one kept.
            for line in self.file.read_text(encoding="utf-8").splitlines():
                record = json.loads(line)
                if "uuid" in record:
                    self.parent = record["uuid"]

    def keep(self, kind: str, message: dict[str, Any] | None, **fields: object) -> None:
        if self.file is None:
            return
        record = {
            "parentUuid": self.parent, "isSidechain": False, "userType": "external",
            "cwd": str(Path.cwd()), "sessionId": _session(), "version": "2.1.140",
            "type": kind, "message": message, "uuid": str(uuid.uuid4()),
            "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            **fields,
        }  # fmt: skip
        if message is None:
            del record["message"]
        self.parent = record["uuid"]
        self.file.parent.mkdir(parents=True, exist_ok=True)
        with self.file.open("a", encoding="utf-8") as out:
            out.write(json.dumps(record) + "\n")


def _write(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def _log(entry: object) -> None:
    path = os.environ.get("FAKE_CLAUDE_LOG")
    if path:
        # Several may write at once (detection asks the version and the login together), and on
        # Windows an append is a seek and then a write: two lines interleaved and broke (found on
        # 18/10/2026). One whole line at a time, under a lock.
        with locked(Path(path + ".lock")), Path(path).open("a", encoding="utf-8") as log:
            log.write(json.dumps(entry) + "\n")


def _ruled(rules: list[str], tool: str, arguments: dict[str, Any]) -> bool:
    """Whether an `ask` rule names this `Bash` command: `Bash(git push)`, or `Bash(git push *)`
    for it with arguments."""
    command = str(arguments.get("command", ""))
    for rule in rules:
        if tool != "Bash" or not rule.startswith("Bash(") or not rule.endswith(")"):
            continue
        prefix = rule[len("Bash(") : -1]
        if command == prefix or (prefix.endswith(" *") and command.startswith(prefix[:-1])):
            return True
    return False


class _InterruptedError(Exception):
    pass


def _auth(arguments: list[str]) -> None:
    """`claude auth status|login`, with the login kept in the file `FAKE_CLAUDE_AUTH` (logged in
    as `someone@example.com` when there is no file). Nobody logged in, the status is written in
    Claude Code 2.1.296's shape, indented, and the exit code is 1, as it does."""
    own = Path(os.environ.get("FAKE_CLAUDE_AUTH", "fake-claude-auth.json"))
    kept = ACCOUNT / "fake-auth.json" if ACCOUNT else own
    if arguments[0] == "login":
        named = arguments.index("--email") + 1 if "--email" in arguments else 0
        email = arguments[named] if named else "other@example.com"
        kept.parent.mkdir(parents=True, exist_ok=True)
        kept.write_text(json.dumps({"loggedIn": True, "email": email}), encoding="utf-8")
        sys.stdout.write("Login successful.\n")
        return
    nobody = {"loggedIn": False}
    status = (
        json.loads(kept.read_text(encoding="utf-8"))
        if kept.exists()
        else nobody
        if ACCOUNT
        else {"loggedIn": True, "email": "someone@example.com", "subscriptionType": "max"}
    )
    if not status.get("loggedIn"):
        status = {"loggedIn": False, "authMethod": "none", "apiProvider": "firstParty"} | status
        sys.stdout.write(json.dumps(status, indent=2) + "\n")
        sys.exit(1)
    sys.stdout.write(json.dumps(status) + "\n")


def main() -> None:
    credentials = sorted(name for name in os.environ if name.startswith("ANTHROPIC_"))
    _log({"argv": sys.argv[1:], "cwd": str(Path.cwd()), "credentials": credentials,
          "account": "" if ACCOUNT is None else str(ACCOUNT)})  # fmt: skip
    if sys.argv[1:2] == ["--version"]:
        sys.stdout.write(f"{os.environ.get('FAKE_CLAUDE_VERSION', '2.1.140')} (Claude Code)\n")
        return
    if sys.argv[1:2] == ["auth"]:
        _auth(sys.argv[2:])
        return
    named = Path(os.environ["FAKE_CLAUDE_SCRIPT"])
    # A folder of scripts holds one per project, named after the folder Claude Code works in; a
    # resumed session reads `<folder>.resume.json` when there is one. A folder with no script of
    # its own takes no prompt: Atlas starts one only to ask its models.
    if named.is_dir():
        resumed = any(argument.startswith("--resume=") for argument in sys.argv)
        again = named / f"{Path.cwd().name}.resume.json"
        named = again if resumed and again.exists() else named / f"{Path.cwd().name}.json"
    loaded = json.loads(named.read_text(encoding="utf-8")) if named.exists() else []
    turns: list[list[dict[str, Any]]] = loaded["turns"] if isinstance(loaded, dict) else [loaded]
    if LIMITED:
        turns = [[{"limit": int(time.time()) + 3600}]]
    arguments = sys.argv[1:]
    mode = [arguments[arguments.index("--permission-mode") + 1]]
    model = [arguments[arguments.index("--model") + 1] if "--model" in arguments else None]
    users = os.environ.get("FAKE_CLAUDE_USER_SETTINGS")
    own = json.loads(Path(users).read_text(encoding="utf-8")) if users else {}
    asked_rules: list[str] = own.get("permissions", {}).get("ask", [])
    inbox: queue.Queue[dict[str, Any] | None] = queue.Queue()

    def read() -> None:
        for line in sys.stdin:
            if line.strip():
                message = json.loads(line)
                _log(message)
                inbox.put(message)
        inbox.put(None)

    threading.Thread(target=read, daemon=True).start()
    counter = [0]
    statuses = [0]
    kept = _Kept()

    def control(message: dict[str, Any]) -> None:
        """Answer Atlas's own requests; an interrupt ends the turn."""
        request = message["request"]
        answer: dict[str, Any] = {}
        if request.get("subtype") == "initialize":
            answer = {
                "commands": _command_rows(),
                "models": MODELS,
                "account": {"email": "someone@example.com", "apiProvider": "firstParty"}
                | ({"subscriptionType": SUBSCRIPTION} if SUBSCRIPTION else {}),
            }
        if request.get("subtype") == "mcp_status":
            statuses[0] += 1
            answer = {"mcpServers": SERVERS + _account_servers(statuses[0])}
        if request.get("subtype") == "get_usage":
            if os.environ.get("FAKE_CLAUDE_USAGE") == "refuse":
                refused = {"subtype": "error", "request_id": message["request_id"],
                           "error": "Unsupported control request subtype: get_usage"}  # fmt: skip
                _write({"type": "control_response", "response": refused})
                return
            answer = _usage()
        response = {"subtype": "success", "request_id": message["request_id"], "response": answer}
        _write({"type": "control_response", "response": response})
        if request.get("subtype") == "set_permission_mode":
            mode[0] = request["mode"]
        if request.get("subtype") == "set_model":
            model[0] = request["model"]
        if request.get("subtype") == "interrupt":
            raise _InterruptedError

    def next_message() -> dict[str, Any]:
        while True:
            message = inbox.get()
            if message is None:
                sys.exit(0)
            if message.get("type") == "control_request":
                control(message)
                continue
            return message

    def ask(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        counter[0] += 1
        request_id = f"cc-{counter[0]}"
        request = {
            "subtype": "can_use_tool",
            "tool_name": tool,
            "input": arguments,
            "tool_use_id": f"toolu_{counter[0]}",
            "title": f"Claude wants to use {tool}",
        }
        if (
            mode[0] == "auto"
            and tool != "AskUserQuestion"
            and not _ruled(asked_rules, tool, arguments)
        ):
            return {"behavior": "allow", "updatedInput": arguments}
        _write({"type": "control_request", "request_id": request_id, "request": request})
        while True:
            message = next_message()
            if message.get("type") == "control_response":
                return message["response"]["response"]
            take(message)

    def assistant(block: dict[str, Any], late: float = 0) -> None:
        body = {"role": "assistant", "content": [block]}
        _write({"type": "assistant", "message": body})
        # The session file keeps each reply's usage, as Claude Code's does.
        body = {**body, "usage": USED}
        if late:
            threading.Timer(late, kept.keep, ("assistant", body)).start()
        else:
            kept.keep("assistant", body)

    def take(queued: dict[str, Any]) -> None:
        """A message that arrived while the turn ran: Atlas's request, or a steer."""
        if queued.get("type") == "control_request":
            control(queued)
        elif queued.get("type") == "user":
            kept.keep("user", {"role": "user", "content": queued["message"]["content"]})
            assistant({"type": "text", "text": f"Steered: {queued['message']['content']}"})

    def result(text: str, *, reason: str = "completed", error: bool = False) -> None:
        _write({"type": "result", "subtype": "success" if not error else "error_during_execution",
                "is_error": error, "result": text, "session_id": _session(), "num_turns": 1,
                "total_cost_usd": 0.0123, "terminal_reason": reason})  # fmt: skip

    turn = 0
    while True:
        message = next_message()
        if message.get("type") != "user":
            continue
        typed = str(message["message"]["content"])
        local = LOCAL.get(typed.removeprefix("/")) if typed.startswith("/") else None
        if local is not None:
            # A command it runs itself: its words, no model, kept as Claude Code keeps it.
            name = typed.removeprefix("/")
            tags = (f"<command-name>/{name}</command-name>\n            "
                    f"<command-message>{name}</command-message>\n            "
                    "<command-args></command-args>")  # fmt: skip
            kept.keep("user", {"role": "user", "content": tags})
            kept.keep("system", None, subtype="local_command",
                      content=f"<local-command-stdout>{local}</local-command-stdout>")  # fmt: skip
            _write({"type": "assistant", "message": {"role": "assistant", "model": "<synthetic>",
                    "content": [{"type": "text", "text": local}]}})  # fmt: skip
            _write({"type": "result", "subtype": "success", "is_error": False, "result": local,
                    "session_id": _session(), "num_turns": 0, "total_cost_usd": 0})  # fmt: skip
            continue
        script = turns[min(turn, len(turns) - 1)]
        turn += 1
        kept.keep("user", {"role": "user", "content": message["message"]["content"]})
        _write({"type": "system", "subtype": "init", "session_id": _session(),
                "model": _runs_as(model[0]), "apiKeySource": "none",
                "slash_commands": COMMANDS})  # fmt: skip
        try:
            for step in script:
                while not inbox.empty():
                    queued = inbox.get_nowait()
                    if queued is None:
                        # Its input closed: the turn ends, then the process (kept for
                        # next_message, which used to wait forever for it).
                        inbox.put(None)
                        break
                    take(queued)
                if "say" in step:
                    assistant({"type": "text", "text": step["say"]}, float(step.get("late", 0)))
                elif "think" in step:
                    assistant({"type": "thinking", "thinking": step["think"], "signature": "s"})
                elif "tool" in step or "questions" in step:
                    tool = step.get("tool", "AskUserQuestion")
                    arguments = step.get("input") or {"questions": step["questions"]}
                    call = f"toolu_call_{counter[0] + 1}"
                    assistant({"type": "tool_use", "id": call, "name": tool, "input": arguments})
                    answer = ask(tool, arguments)
                    allowed = answer.get("behavior") == "allow"
                    if tool == "Write" and allowed:
                        written = Path(arguments["file_path"])
                        written.write_text(arguments["content"], encoding="utf-8")
                    if tool == "AskUserQuestion" and allowed:
                        text = json.dumps(answer["updatedInput"].get("answers"))
                    else:
                        text = "done" if allowed else answer.get("message", "denied")
                    returned = {"role": "user", "content": [
                        {"type": "tool_result", "tool_use_id": call, "content": text,
                         "is_error": not allowed}]}  # fmt: skip
                    _write({"type": "user", "message": returned})
                    kept.keep("user", returned)
                elif "sleep" in step:
                    deadline = time.monotonic() + float(step["sleep"])
                    while time.monotonic() < deadline:
                        try:
                            queued = inbox.get(timeout=0.05)
                        except queue.Empty:
                            continue
                        if queued is None:
                            sys.exit(0)
                        take(queued)
                elif "compact" in step:
                    before, after = step["compact"]
                    _write(
                        {
                            "type": "system",
                            "subtype": "compact_boundary",
                            "session_id": _session(),
                            "compact_metadata": {
                                "trigger": "auto",
                                "pre_tokens": before,
                                "post_tokens": after,
                            },
                        }
                    )
                    logical, kept.parent = kept.parent, None
                    kept.keep(
                        "system",
                        None,
                        subtype="compact_boundary",
                        content="Conversation compacted",
                        logicalParentUuid=logical,
                        compactMetadata={
                            "trigger": "auto",
                            "preTokens": before,
                            "postTokens": after,
                        },
                    )
                    kept.keep(
                        "user",
                        {
                            "role": "user",
                            "content": "This session is being "
                            "continued from a previous conversation. Summary: ...",
                        },
                        isCompactSummary=True,
                        isVisibleInTranscriptOnly=True,
                    )
                elif "commands_changed" in step:
                    rows = [{"name": name, "description": f"The {name} skill"}
                            for name in step["commands_changed"]]  # fmt: skip
                    _write({"type": "system", "subtype": "commands_changed",
                            "commands": COMMAND_ROWS + rows, "session_id": _session()})  # fmt: skip
                elif "account" in step:
                    said = ACCOUNT.name if ACCOUNT else "own"
                    assistant({"type": "text", "text": f"Running on {said}."})
                elif "usage" in step:
                    session, week = step["usage"]
                    windows = {
                        "five_hour": {"utilization": session, "resetsAt": 1792350600},
                        "seven_day": {"utilization": week, "resetsAt": 1792742400},
                    }
                    info = {"status": "allowed", "resetsAt": 1792350600,
                            "rateLimitType": "five_hour", "unifiedWindows": windows}  # fmt: skip
                    _write({"type": "rate_limit_event", "rate_limit_info": info})
                elif "limit" in step:
                    _write({"type": "rate_limit_event", "rate_limit_info": {
                        "status": "rejected", "resetsAt": step["limit"]}})  # fmt: skip
                    _write({"type": "assistant", "error": "rate_limit", "message": {
                        "role": "assistant", "content": [
                            {"type": "text", "text": "Claude usage limit reached."}]}})  # fmt: skip
                    result("Claude usage limit reached.", error=True)
                    break
                elif "result" in step:
                    result(step["result"])
                    break

        except _InterruptedError:
            result("Interrupted by the user.", reason="aborted_streaming", error=True)


if __name__ == "__main__":
    main()
