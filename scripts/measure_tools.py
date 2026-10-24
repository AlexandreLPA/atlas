"""What the always-on tools cost per model call, in tokens.

Without arguments it prints Atlas's own estimate (UTF-8 bytes of the definitions over four).
With a provider it asks the real model: the same short request with and without the tools, and
with each tool alone when `--each` is given, and prints the difference the provider counted.
Every call asks for 16 output tokens at most and lands in the call ledger of the real home.

    uv run python scripts/measure_tools.py
    uv run python scripts/measure_tools.py openai [model] [--each]

`--claude-code` measures delegate as it is with Claude Code installed, and `--browser` the tool
search with browser_exec behind it, as with a Chrome installed.
"""

import asyncio
import dataclasses
import json
import math
import os
import sys
import tempfile
from pathlib import Path

from proof_ledger import ensure_ledger  # the scripts' folder, on the path when run

from atlas.agent import Tool, Toolset
from atlas.browser import Browser
from atlas.conversations import ConversationStore
from atlas.home import Database, Redactor, load_settings, open_home
from atlas.jobs import Task
from atlas.memory import MemoryFiles
from atlas.models import (
    CallLedger,
    ModelRequest,
    Models,
    ToolSpec,
    UserMessage,
    load_catalog,
)
from atlas.secrets import Vault
from atlas.skills import SkillLibrary
from atlas.tools import (
    Answers,
    Commands,
    Outcome,
    Plan,
    Question,
    WebFetch,
    Workspace,
    ask_user,
    conversation_search,
    delegate,
    file_tools,
    find_files,
    install_tool_search,
    memory,
    skill_manage,
    web_search,
)
from atlas.tools.keyless import KeylessSearch

#: The target for the always-on tools, from the roadmap (V3 paid about 10,800).
#: The executor delegate names when Claude Code is installed (its plugin's).
CLAUDE_CODE = "claude-code"
TARGET = 3_000


class _Delegator:
    """Nothing behind it: only the tool's definition is measured."""

    async def spawn(self, tasks: tuple[Task, ...], executor: str | None = None) -> str:
        return ""

    def executors(self) -> list[str]:
        """The outside executors installed here: with Claude Code, delegate says it exists."""
        return [CLAUDE_CODE] if "--claude-code" in sys.argv else []

    async def resume(self, subagent_id: str, message: str) -> str:
        return ""

    async def account(self, email: str) -> str:
        return ""

    def list(self) -> str:
        return ""

    async def steer(self, subagent_id: str, message: str) -> str:
        return ""

    async def stop(self, subagent_id: str) -> str:
        return ""


async def _never(questions: tuple[Question, ...]) -> Answers:
    return Answers(Outcome.UNDELIVERED)


async def _nothing(arguments: dict[str, object]) -> str:
    return ""


def always_on(root: Path) -> list[Tool]:
    """The always-on tools as a conversation gets them, with one MCP server behind tool search
    and a conversation's subagents behind delegate, so both are there."""
    workspace = Workspace(root)
    tools = [
        *file_tools(workspace),
        find_files(workspace),
        Commands(workspace, root / "commands").tool(),
        web_search(KeylessSearch()),
        WebFetch().tool(),
        ask_user(_never),
        Plan().tool(),
        memory(MemoryFiles(root / "memory")),
        skill_manage(SkillLibrary(root / "skills")),
        conversation_search(ConversationStore(Database(root / "atlas.db"))),
    ]
    executor = delegate(_Delegator())

    if executor is not None:
        tools.append(executor)
    deferred = [Tool("mcp__github__create_issue", "Create an issue.", {}, _nothing, deferred=True,
                     source="mcp:github")]  # fmt: skip
    if "--browser" in sys.argv:
        # browser_exec waits behind tool search: only its name and first sentence are there.
        browser = Browser(root / "browser", ["chrome"], redact=str, notify=lambda _c, _s: None)
        deferred.append(browser.tool("measure", sees_images=True))
    toolset = Toolset([*tools, *deferred])
    install_tool_search(toolset, {"mcp:github": "Issues and pull requests on GitHub."})
    return list(toolset.always)


def estimate(tool: Tool) -> int:
    spec = ToolSpec(tool.name, tool.description, tool.parameters)
    return math.ceil(len(json.dumps(dataclasses.asdict(spec)).encode()) / 4)


async def _count(models: Models, provider: str, model: str | None, tools: list[Tool]) -> int:
    client = models.client(provider, model)
    specs = tuple(ToolSpec(t.name, t.description, t.parameters) for t in tools)
    request = ModelRequest(
        (UserMessage("Say ok."),), specs, purpose="measure", max_output_tokens=16
    )
    reply = await client.reply(request)
    if reply.usage is None:
        raise SystemExit(f"{provider} reported no usage.")
    return reply.usage.input_tokens


async def _measure(provider: str, model: str | None, each: bool, tools: list[Tool]) -> None:
    settings = load_settings(os.environ).settings
    home = open_home(settings.home)
    vault = Vault(home.secrets_folder, settings.vault, Redactor())
    database = Database(home.database_file)
    ensure_ledger(database)
    ledger = CallLedger(database)
    models = Models(load_catalog(home.models_file), vault, settings.model, ledger=ledger)
    try:
        bare = await _count(models, provider, model, [])
        full = await _count(models, provider, model, tools)
        _say(f"{provider} {model or '(default)'}: {full - bare} tokens for {len(tools)} tools")
        if each:
            for tool in tools:
                alone = await _count(models, provider, model, [tool])
                _say(f"  {tool.name:<12} {alone - bare:>5}")
    finally:
        await models.close()


def main() -> None:
    arguments = [a for a in sys.argv[1:] if not a.startswith("--")]
    with tempfile.TemporaryDirectory() as folder:
        tools = always_on(Path(folder))
        total = 0
        for tool in tools:
            total += estimate(tool)
            _say(f"{tool.name:<12} {estimate(tool):>5}")
        _say(f"{'estimate':<12} {total:>5} of {TARGET:,}")
        if arguments:
            provider = arguments[0]
            model = arguments[1] if len(arguments) > 1 else None
            asyncio.run(_measure(provider, model, "--each" in sys.argv, tools))


def _say(line: str) -> None:
    # The Windows console's code page cannot write every character.
    sys.stdout.buffer.write((line + "\n").encode("utf-8"))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
