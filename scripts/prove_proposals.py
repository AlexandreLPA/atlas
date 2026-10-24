"""Prove the background review's memory proposals end to end against a real model. Spends a little
credit.

    uv run python scripts/prove_proposals.py openai gpt-6-luna
    uv run python scripts/prove_proposals.py gemini gemini-3.7-flash --pace 15
    uv run python scripts/prove_proposals.py --rehearse

Step 9b (docs/ARCHITECTURE.md, Memory proposals) through the real app's window, as
`scripts/prove_desktop.py` proves step 9: the app starts the real `atlas serve` on a temporary home,
and Playwright clicks in its window (`apps/desktop/e2e/prove/proposals.prove.ts`). Three steps:

1. a conversation contradicts the profile, and the review proposes the change instead of making
   it: the review's row says so with "Review", the card shows in the conversation, and the files
   are as they were;
2. approving the card applies it, and the card says what it overwrote or removed;
3. a second conversation's proposal, whose entries the user then edits by hand, is marked when the
   list opens, offers only to discard, and discarding leaves the memory as the user left it.

The profile and the memory start almost full, as unattended reviews meet them: no fact a review
would add fits, so it must replace or remove. Each conversation holds nine earlier messages, so the
tenth, sent from the window, makes the review due.

The key and the call ledger travel as in `prove_desktop.py`. `--rehearse` runs the same steps
against the fake model server, scripted as the real model is expected to answer: no key, no credit.

Exit code 0 only when every step did what it should.
"""

import argparse
import os
import sys
import tempfile
from pathlib import Path

from prove_desktop import carry_ledger, remove_home, run_proof, say, seed_conversation

from atlas.__main__ import SCHEMAS
from atlas.home import (
    LOG_FILE,
    Database,
    Redactor,
    configure_logging,
    load_settings,
    migrate,
    open_home,
)
from atlas.memory import LIMITS, Action, MemoryFiles, Operation, Target, size
from atlas.models import load_catalog
from atlas.secrets import Vault

#: The profile: who the user is, with what the first conversation contradicts (where she lives,
#: how she wants answers), what it makes stale (the plan to move), and what the second one
#: contradicts (her work).
USER = [
    "Nome: Marina Duarte. Designer gráfica há 8 anos, trabalha como freelancer.",
    "Mora no Porto, Portugal; fuso horário de Lisboa.",
    "Prefere respostas longas e detalhadas, com exemplos e referências.",
    "Usa Figma e Illustrator no dia a dia; não usa Photoshop.",
    "Cliente principal: Editora Atlântica, para quem faz capas de livros.",
    "Gosta de paletas com verde e cinza; evita vermelho nos projetos.",
    "Trabalha melhor de manhã, entre 7h e 12h; à tarde faz reuniões.",
    "Está montando um portfólio novo para publicar em outubro de 2026.",
    "Fala português; escreve para clientes estrangeiros em inglês.",
    "Tem um gato chamado Pixel, que aparece nas chamadas de vídeo.",
    "Prefere receber orçamentos em euros, com IVA separado.",
    "Faz aulas de cerâmica às quintas-feiras à noite.",
    "Cobra por projeto, não por hora; pede 50% de entrada.",
    "Gosta de exemplos visuais quando aprende uma ferramenta nova.",
    "Usa um iPad Pro com Apple Pencil para esboços.",
    "Evita reuniões às sextas-feiras.",
    "Quer aprender motion design em 2027.",
    "Toma café sem açúcar e trabalha ouvindo jazz.",
    "Planeja mudar do Porto para Lisboa até o fim de 2026.",
    "Não gosta de emojis em textos de trabalho.",
    "Assina a newsletter Tipografia Hoje e lê aos domingos.",
    "Prefere chamadas de vídeo curtas, de no máximo 30 minutos.",
    "Organiza as tarefas no Notion, num quadro por cliente.",
    "Usa óculos para trabalhar à noite.",
]
_DELIVERIES = [(3, 2), (17, 2), (5, 3), (21, 3), (9, 4), (30, 4), (14, 5), (2, 6)]
_DELIVERIES += [(19, 6), (8, 7), (27, 7), (11, 8), (25, 8), (6, 9), (22, 9), (3, 9)]
#: The memory: environment facts, and a log of finished deliveries that the memory tool's
#: description says to skip, which a review consolidating would rather remove.
MEMORY = [
    "Pasta de trabalho da Marina: ~/Design; cada cliente em ~/Design/Clientes/<cliente>/<ano>.",
    "A gráfica da Editora Atlântica pede PDF/X-4, sangria de 3 mm e marcas de corte.",
    "As fontes licenciadas ficam em ~/Design/Fontes; não usar fontes de sites gratuitos.",
    "Aprovações de capa chegam por e-mail.",
    *(
        f"Capa {n} da Editora Atlântica entregue em {day:02d}/{month:02d}/2026; arquivo final "
        f"em ~/Design/Clientes/Atlantica/2026/capa-{n}.pdf."
        for n, (day, month) in enumerate(_DELIVERIES, start=1)
    ),
    "Backup diário no NAS.",
]
#: The most room a seed leaves: less than any fact a review would add.
ROOM = 12

FIRST_TITLE = "Mudança para Lisboa"
FIRST = [
    "Oi! Lembra de mim? Sou a Marina.",
    "Tenho novidades: me mudei para Lisboa em setembro, já estou instalada.",
    "E mudei de ideia: agora prefiro respostas curtas, em tópicos. As longas me tomam tempo.",
    "Continuo com a Editora Atlântica como cliente principal.",
    "O portfólio novo sai mesmo em outubro.",
    "Em Lisboa estou trabalhando num coworking no Cais do Sodré.",
    "Ainda uso Figma e Illustrator.",
    "Meu horário continua o mesmo, de manhã.",
    "Vou precisar de ajuda com textos curtos para o portfólio.",
]
SECOND_TITLE = "Livro sobre o mar"
SECOND = [
    "Oi, é a Marina de novo.",
    "Grande mudança: larguei o design gráfico, agora sou ilustradora de livros infantis.",
    "Não faço mais capas para a Editora Atlântica; meu cliente agora é a Editora Pirilampo.",
    "Troquei o Illustrator pelo Procreate no iPad.",
    "Estou ilustrando um livro sobre o mar para crianças de 5 anos.",
    "Quero personagens simpáticos e cores suaves.",
    "O prazo é dezembro.",
    "Gosto de pesquisar referências antes de desenhar.",
    "Vou te pedir ideias de personagens.",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Prove the review's memory proposals.")
    parser.add_argument("provider", nargs="?")
    parser.add_argument("model", nargs="?")
    parser.add_argument("--pace", type=float, default=0.0, help="seconds to wait between steps")
    parser.add_argument("--rehearse", action="store_true", help="the fake model, no credit")
    args = parser.parse_args()
    if not args.rehearse and not (args.provider and args.model):
        parser.error("name a provider and a model, or --rehearse")
    settings = load_settings(os.environ).settings
    real = open_home(settings.home)
    redactor = Redactor()
    configure_logging("WARNING", redactor)

    root = Path(tempfile.mkdtemp(prefix="atlas-prove-proposals-"))
    try:
        home = root / "home"
        work = root / "work"
        work.mkdir()
        prepared = open_home(home)
        migrate(Database(prepared.database_file), SCHEMAS)
        _seed_memory(MemoryFiles(prepared.memory_folder))
        seed_conversation(prepared.database_file, FIRST, FIRST_TITLE)
        seed_conversation(prepared.database_file, SECOND, SECOND_TITLE)
        environ = {
            **os.environ,
            "ATLAS_PROVE_HOME": str(home),
            "ATLAS_PROVE_WORK": str(work),
            "ATLAS_PROVE_PACE": str(args.pace),
            "ATLAS_PROVE_FIRST_TITLE": FIRST_TITLE,
            "ATLAS_PROVE_SECOND_TITLE": SECOND_TITLE,
        }
        if args.rehearse:
            environ["ATLAS_PROVE_REHEARSE"] = "1"
        else:
            vault = Vault(real.secrets_folder, settings.vault, redactor)
            key = load_catalog(real.models_file).provider(args.provider).key
            if key is not None:
                value = vault.get(key)
                if value is None:
                    sys.stderr.write(f"There is no {key} in the vault of {real.root}.\n")
                    return 1
                Vault(prepared.secrets_folder, settings.vault, redactor).set(key, value)
            environ["ATLAS_PROVE_PROVIDER"] = args.provider
            environ["ATLAS_PROVE_MODEL"] = args.model
        proof = run_proof("proposals.prove.ts", environ)
        if not args.rehearse:
            carry_ledger(prepared.database_file, real.database_file)
        if proof != 0:
            _show_reviews(prepared.logs_folder / LOG_FILE)
    finally:
        remove_home(root)
    say("PROVED: every step did what it should." if proof == 0 else "FAILED.")
    return proof


def _show_reviews(log: Path) -> None:
    """What the server said about its reviews, for a failed proof: the home goes with the run."""
    try:
        lines = log.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        say(f"no server log: {error}")
        return
    for line in lines:
        if "review" in line.lower() or '"level": "ERROR"' in line:
            say(f"server: {line[:400]}")


def _seed_memory(files: MemoryFiles) -> None:
    """The profile and the memory almost full, written through the store, its checks included."""
    for target, entries in ((Target.USER, USER), (Target.MEMORY, MEMORY)):
        room = LIMITS[target] - size(entries)
        if not 0 <= room < ROOM:
            raise SystemExit(
                f"The {target} seed leaves {room} characters free; keep it under {ROOM}."
            )
        applied = files.change(target, [Operation(Action.ADD, entry) for entry in entries])
        if applied.entries is None:
            raise SystemExit(f"The {target} seed was refused: {applied.error}")


if __name__ == "__main__":
    raise SystemExit(main())
