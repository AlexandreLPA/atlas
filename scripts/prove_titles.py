"""Prove the title models against the real ones. Spends almost nothing: two calls of about 600
tokens in and 20 out, under US$ 0.0002 together; at worst, a model writing all 512 tokens it may,
US$ 0.0006 (prices of 14/10/2026).

    uv run python scripts/prove_titles.py

On the user's real home (`ATLAS_HOME`), the keys from the vault, both calls in the call ledger.
Each title model (atlas.assistant.titles) gets the same request a conversation's first turn sends,
the message in Portuguese, and proves:

1. The request is taken: Claude Haiku 5.5 with `thinking: disabled`, GPT-6 Luna with the effort
   `none` (a 400 would leave every conversation with its instant title).
2. Nothing was spent reasoning: no reasoning tokens, and a reply of a few dozen tokens.
3. The reply reads as a title in the user's language.

A model whose provider has no key in the vault is skipped and said. Exit code 0 only when every
model with a key holds all three, and at least one had a key.
"""

import asyncio
import os
import sys

from proof_ledger import ensure_ledger  # the scripts' folder, on the path when run

from atlas.assistant.titles import TITLE_MODELS, read_title, title_request
from atlas.home import Database, Redactor, configure_logging, load_settings, open_home
from atlas.models import CallLedger, Models, ModelsError, load_catalog
from atlas.secrets import Vault

MESSAGE = (
    "me ajuda a configurar o nginx no meu servidor ubuntu com https, usando o certbot, e deixar "
    "a renovação automática do certificado funcionando"
)
#: More than this is a model reasoning, or answering instead of naming.
MOST_OUTPUT = 60


def main() -> int:
    settings = load_settings(os.environ).settings
    home = open_home(settings.home)
    redactor = Redactor()
    configure_logging("WARNING", redactor)
    database = Database(home.database_file)
    ensure_ledger(database)
    ledger = CallLedger(database)
    vault = Vault(home.secrets_folder, settings.vault, redactor)
    models = Models(load_catalog(home.models_file), vault, settings.model, ledger=ledger)
    results = asyncio.run(_prove(models))
    proved = bool(results) and all(results)
    _say("PROVED: every title model took the request, did not reason, and titled."
         if proved else "FAILED.")  # fmt: skip
    return 0 if proved else 1


async def _prove(models: Models) -> list[bool]:
    results: list[bool] = []
    try:
        keyed = {provider.id for provider, stored in models.keyed() if stored}
        for provider, model in TITLE_MODELS:
            if provider not in keyed:
                _say(f"{provider} {model}: no key in the vault, skipped")
                continue
            try:
                reply = await models.client(provider, model).reply(title_request(MESSAGE, "prove"))
            except ModelsError as error:
                _say(f"{provider} {model}: refused: {error}")
                results.append(False)
                continue
            usage = reply.usage
            title = read_title(reply.text)
            _say(f"{provider} {model}: {reply.text!r} -> {title!r}")
            if usage is not None:
                _say(
                    f"  input {usage.input_tokens}, output {usage.output_tokens}, reasoning "
                    f"{usage.reasoning_tokens}, cost US$ {reply.cost.usd}"
                )
            quiet = (
                usage is not None
                and usage.reasoning_tokens == 0
                and usage.output_tokens <= MOST_OUTPUT
            )
            if not quiet:
                _say("  it reasoned, or wrote too much")
            results.append(quiet and title is not None)
    finally:
        await models.close()
    return results


def _say(text: str) -> None:
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    sys.exit(main())
