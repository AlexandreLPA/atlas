"""Prove one provider end to end against the real model. Spends a little credit.

    uv run python scripts/prove_models.py openai gpt-5.6-luna --effort low

The same path the agent loop will take, on the user's real home (`ATLAS_HOME`): the key comes from
the vault and is checked where it costs nothing; then the model is asked to read a file through a
tool, gets the tool's result back with whatever provider fields the first reply carried (Gemini's
thought signature, OpenRouter's reasoning details, OpenAI's encrypted reasoning), and answers.
Both calls land in the call ledger, which is printed at the end.

Exit code 0 only when the model called the tool, took its result, and answered from it.
"""

import argparse
import asyncio
import os
import sys

from proof_ledger import LEDGER_ROWS, ensure_ledger  # the scripts' folder, on the path when run

from atlas.home import Database, Redactor, configure_logging, load_settings, open_home
from atlas.models import (
    CallLedger,
    ModelReply,
    ModelRequest,
    Models,
    ModelsError,
    SystemMessage,
    ToolResult,
    ToolSpec,
    UserMessage,
    load_catalog,
)
from atlas.secrets import Vault

READ_FILE = ToolSpec(
    name="read_file",
    description="Read a text file from the user's notes and return its content.",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Path of the file to read."}},
        "required": ["path"],
    },
)

FILE_CONTENT = "Buy bread. Call the plumber at 3 pm."


def main() -> int:
    parser = argparse.ArgumentParser(description="Prove one provider end to end.")
    parser.add_argument("provider")
    parser.add_argument("model", nargs="?")
    parser.add_argument("--effort")
    parser.add_argument("--max-output", type=int, default=2000)
    args = parser.parse_args()

    settings = load_settings(os.environ).settings
    home = open_home(settings.home)
    redactor = Redactor()
    configure_logging("WARNING", redactor)
    database = Database(home.database_file)
    ensure_ledger(database)
    ledger = CallLedger(database)
    vault = Vault(home.secrets_folder, settings.vault, redactor)
    models = Models(load_catalog(home.models_file), vault, settings.model, ledger=ledger)
    before = len(ledger.recent(LEDGER_ROWS))
    try:
        proved = asyncio.run(
            _prove(models, args.provider, args.model, args.effort, args.max_output)
        )
    finally:
        _ledger(ledger, before)
    return 0 if proved else 1


async def _prove(
    models: Models, provider: str, model: str | None, effort: str | None, max_output: int
) -> bool:
    try:
        check = await models.check_key(provider)
        _say(f"key check: {check.status.value}, {check.detail}")
        if not check.usable:
            return False
        client = models.client(provider, model, effort=effort)
        _say(f"model: {client.provider.name} {client.model}, wire {client.provider.wire.value}")
        question = ModelRequest(
            (
                SystemMessage("You are a helpful assistant. Use the tools you are given."),
                UserMessage("What does my file notes/today.md say? Read it before answering."),
            ),
            (READ_FILE,),
            purpose="proof",
            max_output_tokens=max_output,
        )
        first = await client.reply(question)
        _report("call 1", first)
        if not first.tool_calls:
            _say("FAILED: the model did not call the tool.")
            return False
        call = first.tool_calls[0]
        answer = await client.reply(
            ModelRequest(
                (*question.messages, first.message, ToolResult(call.id, FILE_CONTENT)),
                (READ_FILE,),
                purpose="proof",
                max_output_tokens=max_output,
            )
        )
        _report("call 2", answer)
        if "bread" not in answer.text.lower():
            _say("FAILED: the answer does not come from the tool's result.")
            return False
        _say("PROVED: tool call, tool result with the provider fields sent back, and the answer.")
        return True
    except ModelsError as error:
        _say(f"FAILED: {type(error).__name__}: {error}")
        return False
    finally:
        await models.close()


def _report(label: str, reply: ModelReply) -> None:
    message = reply.message
    _say(f"{label}: finish {reply.finish.value}, served by {reply.served_model}")
    if message.text:
        _say(f"  text: {message.text[:300]!r}")
    if message.reasoning:
        _say(f"  readable reasoning: {len(message.reasoning)} characters")
    for call in message.tool_calls:
        fields = ", ".join(call.provider_fields) or "none"
        _say(f"  tool call: {call.name}({call.arguments}) id={call.id} provider fields: {fields}")
    _say(f"  message provider fields: {', '.join(message.provider_fields) or 'none'}")
    output = message.provider_fields.get("output")
    if isinstance(output, list):
        kinds = [str(item.get("type")) for item in output if isinstance(item, dict)]
        sealed = sum(
            1 for item in output if isinstance(item, dict) and item.get("encrypted_content")
        )
        _say(f"  output items: {', '.join(kinds)}; with encrypted reasoning: {sealed}")
    usage = reply.usage
    if usage is None:
        _say("  usage: not reported")
    else:
        _say(
            f"  usage: input {usage.input_tokens} (cache read {usage.cache_read_tokens}, write "
            f"{usage.cache_write_tokens}), output {usage.output_tokens} (reasoning "
            f"{usage.reasoning_tokens})"
        )
    _say(f"  cost: {reply.cost.usd} USD ({reply.cost.source.value})")


def _ledger(ledger: CallLedger, before: int) -> None:
    calls = ledger.recent(LEDGER_ROWS)
    new = list(reversed(calls[: len(calls) - before]))
    _say(f"call ledger, {len(new)} new rows:")
    for call in new:
        tokens = (
            "-" if call.usage is None else f"{call.usage.input_tokens}/{call.usage.output_tokens}"
        )
        _say(
            f"  {call.started_at} {call.provider} {call.model} {call.purpose} {call.outcome.value} "
            f"{call.duration_ms} ms tokens {tokens} cost {call.cost.usd} ({call.cost.source.value})"
        )


def _say(line: str) -> None:
    # A model's answer may hold characters the Windows console's code page cannot write.
    sys.stdout.buffer.write((line + "\n").encode("utf-8"))
    sys.stdout.flush()


if __name__ == "__main__":
    sys.exit(main())
