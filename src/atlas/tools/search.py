"""Ranking deferred tools for a tool search. Pure: text in, names out.

The rules:

- **BM25 over a short document per tool:** the words of its name, its source ("github"), its
  description and the names of its top-level parameters. Schema bodies are noise with no recall
  gain. Words are stemmed, so "issues" finds `create_issue`.
- **The rarest word of the query admits a tool.** BM25 adds up every shared word, so on a large
  catalog any score above zero lets in tools that share only "send" or "email". The word that
  names the intent is the one few tools carry, and a tool without it answers another question.
- **A long query needs half of its known words.** Without it a hunt whose every word exists
  somewhere ("run shell command execute code python") is answered by a tool sharing only "run",
  and the model searches again and again. Short queries are exempt: "list issues" may differ from
  a tool by a word.
- **An exact name ranks first.**

The index is rebuilt from the live tools on every search, never cached per conversation: a
cached catalog drifts and drops tools in silence. A few hundred tools take
milliseconds.
"""

import math
import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache

import snowballstemmer

#: How many tools a search returns when the model does not say (Anthropic sees
#: 3 to 5 tools needed per task).
DEFAULT_LIMIT = 5
MAX_LIMIT = 10
#: A query with at least this many known words must match half of them.
COVERAGE_FROM_TERMS = 4
COVERAGE = 0.5

_WORD = re.compile(r"[A-Za-z0-9]+")
_SEPARATORS = re.compile(r"[_.:-]")
_STEMMER = snowballstemmer.stemmer("english")


@dataclass(frozen=True, slots=True)
class Searchable:
    """What a search reads of one deferred tool."""

    name: str
    description: str
    source: str
    parameters: tuple[str, ...] = ()


@lru_cache(maxsize=16_384)
def _stem(word: str) -> str:
    return _STEMMER.stemWord(word)


def words(text: str) -> list[str]:
    """Lowercase alphanumeric words, stemmed: the same for the index and the query."""
    return [_stem(word.lower()) for word in _WORD.findall(text)]


def _document(tool: Searchable) -> list[str]:
    name = _SEPARATORS.sub(" ", tool.name)
    label = source_label(tool.source)
    extra = label if label and label not in name.split() else ""
    return words(f"{name} {extra} {tool.description} {' '.join(tool.parameters)}")


def source_label(source: str) -> str:
    """The name a person would use for a source: `mcp:github` is `github`."""
    return source.split(":", 1)[1] if ":" in source else source


def unknown_words(tools: Sequence[Searchable], query: str) -> list[str]:
    """The words of `query` no tool carries, as written: each one alone makes a search find
    nothing, so the model is told which to drop."""
    known: set[str] = set()
    for tool in tools:
        known.update(_document(tool))
    return [word for word in _WORD.findall(query) if _stem(word.lower()) not in known]


def rank(tools: Sequence[Searchable], query: str, limit: int = DEFAULT_LIMIT) -> list[Searchable]:
    """The best `limit` tools for `query`, best first; none when nothing answers it."""
    query_words = words(query)
    if not tools or not query_words or limit <= 0:
        return []
    documents = [_document(tool) for tool in tools]
    lengths = [len(document) for document in documents]
    average = sum(lengths) / len(lengths)
    frequency = Counter(word for document in documents for word in set(document))
    count = len(documents)

    def idf(word: str) -> float:
        seen = frequency.get(word, 0)
        return math.log(1 + (count - seen + 0.5) / (seen + 0.5))

    gate = max(query_words, key=idf)
    known = {word for word in query_words if frequency.get(word, 0) > 0}
    needed = 1 if len(known) < COVERAGE_FROM_TERMS else math.ceil(len(known) * COVERAGE)
    exact = query.strip().lower()

    scored: list[tuple[float, int]] = []
    for index, (tool, document) in enumerate(zip(tools, documents, strict=True)):
        if tool.name.lower() == exact:
            scored.append((math.inf, index))
            continue
        present = set(document)
        if gate not in present or sum(1 for word in known if word in present) < needed:
            continue
        scored.append((_bm25(query_words, document, average, idf), index))
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    return [tools[index] for _, index in scored[:limit]]


def _bm25(
    query: list[str],
    document: list[str],
    average: float,
    idf: Callable[[str], float],
    k1: float = 1.5,
    b: float = 0.75,
) -> float:
    counts = Counter(document)
    length = len(document)
    score = 0.0
    for word in query:
        seen = counts.get(word, 0)
        if seen:
            score += (
                idf(word) * seen * (k1 + 1) / (seen + k1 * (1 - b + b * length / max(average, 1.0)))
            )
    return score


def first_sentence(text: str, limit: int = 120) -> str:
    """A description's first sentence, cut at a word when longer than `limit`."""
    flat = " ".join(text.split())
    end = re.search(r"(?<!\be\.g)(?<!\bi\.e)(?<!\betc)[.!?](?=\s|$)", flat)
    flat = flat[: end.end()] if end else flat
    if len(flat) <= limit:
        return flat
    cut = flat[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(",;: ") + "…"


# -- the listing in the tool search's description ------------------------------------------------

#: The budget for the listing: 5% of the model's window, at most 4,000 tokens.
LISTING_SHARE = 0.05
LISTING_MAX_TOKENS = 4_000
#: The cut of each tool's first sentence in the listing.
LISTED_DESCRIPTION_CHARS = 60


def listing_budget(window_tokens: int | None) -> int:
    """The listing's budget in tokens: 5% of the window, at most 4,000."""
    if window_tokens is None:
        return LISTING_MAX_TOKENS
    return min(LISTING_MAX_TOKENS, int(window_tokens * LISTING_SHARE))


def catalog_listing(
    tools: Sequence[Searchable], about: Mapping[str, str], max_tokens: int
) -> str | None:
    """The deferred tools by source: each tool's name and the
    start of its first sentence, sorted so the text is byte-stable and the cache holds. Over
    budget it falls to names only, then sums up the largest sources first, so one huge server
    does not cost a small one its listing; `None` when even that does not fit.

    `about` gives one line per source (an MCP server's instructions).
    """
    groups: dict[str, list[tuple[str, str]]] = {}
    for tool in tools:
        short = first_sentence(tool.description, LISTED_DESCRIPTION_CHARS)
        groups.setdefault(tool.source, []).append((tool.name, short))
    if not groups:
        return None

    def render(source: str, mode: str) -> str:
        entries = sorted(groups[source])
        label = source_label(source)
        line = about.get(source, "")
        head = f"{label} ({len(entries)} tools){': ' + line if line else ''}"
        if mode == "summary":
            return f"{head} - names not listed; search for them"
        if mode == "names":
            return f"{head}\n" + ", ".join(name for name, _ in entries)
        return f"{head}\n" + "\n".join(
            f"- {name}: {short}" if short else f"- {name}" for name, short in entries
        )

    def fits(modes: dict[str, str]) -> str | None:
        text = "\n".join(render(source, modes[source]) for source in sorted(groups))
        return text if math.ceil(len(text) / 4) <= max_tokens else None

    for mode in ("full", "names"):
        modes = dict.fromkeys(groups, mode)
        if (text := fits(modes)) is not None:
            return text
    modes = dict.fromkeys(groups, "names")
    for source in sorted(groups, key=lambda s: (-len(render(s, "names")), s)):
        modes[source] = "summary"
        if (text := fits(modes)) is not None:
            return text
    return None
