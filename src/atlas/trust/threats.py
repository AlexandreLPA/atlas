# SPDX-FileCopyrightText: 2025 Nous Research
# SPDX-FileCopyrightText: 2026 the Atlas contributors
# SPDX-License-Identifier: MIT
"""Spotting text that tries to steer the agent, smuggle out secrets or plant itself. Pure.

Every rule belongs to one scope, and a wider scope runs the narrower ones too:

- `ALL` runs on any text. It catches the plain injection phrases ("ignore previous
  instructions") and secrets read or shipped out through curl, wget or cat.
- `CONTEXT` is for text the user did not write, such as a tool's result or a file read into the
  context. On top of `ALL` it looks for role takeovers and the vocabulary of promptware that
  phones home to a command server.
- `STRICT` is for writes a person can fix when one is refused: memory and skills. On top of
  `CONTEXT` it flags SSH backdoors, secrets written inline, and edits to the agent's own
  instruction files.

The paths guarded are Atlas's own: the vault, the home's settings, and the soul, `SOUL.md`
(added on 15/10/2026, with the soul).

A new rule needs words only an attack uses, or an action only an attack takes. Firm wording alone
is not enough: an honest AGENTS.md says "you must" all the time.
"""

import re
import unicodedata
from enum import StrEnum

#: The scan only advises, so it reads at most this much of a text and its cost stays bounded.
MAX_SCAN_CHARS = 65_536

#: Zero-width and bidirectional controls that hide text from a reader: U+200B to U+200D, the
#: word joiner, the invisible operators U+2062 to U+2064, the byte-order mark, the embeddings and
#: overrides U+202A to U+202E, and the isolates U+2066 to U+2069.
INVISIBLE_CHARS = frozenset("​‌‍⁠⁢⁣⁤﻿‪‫‬‭‮⁦⁧⁨⁩")


class Scope(StrEnum):
    ALL = "all"
    CONTEXT = "context"
    STRICT = "strict"


#: Up to eight words between two words of a rule. A bounded gap, because an open `(?:\w+\s+)*`
#: backtracks for a long time on a near miss.
_GAP = r"(?:\w+\s+){0,8}"
#: A shell variable named like a secret, such as `$API_KEY` or `${GH_TOKENS}`.
_SECRET_VAR = r"\$\{?\w*(?:CREDENTIAL|PASSWORD|SECRET|TOKEN|KEY)S?\b"
#: A verb that edits a file, then the rest of the line up to the file's name.
_EDIT = r"(?:append|add\s+to|change|edit|modify|update|write)\s+[^\n]{0,2048}"

#: The rules in the order they are reported, each block with its scope. The order matters: the
#: first finding is the one a refusal names.
_BLOCKS: tuple[tuple[Scope, tuple[tuple[str, str], ...]], ...] = (
    (Scope.ALL, (
        # The textbook injection phrases.
        ("prompt_injection", rf"ignore\s+{_GAP}(?:prior|above|all|previous)\s+{_GAP}instructions"),
        ("sys_prompt_override", r"system\s+prompt\s+override"),
        ("disregard_rules", rf"disregard\s+{_GAP}(?:any|all|your)\s+{_GAP}(?:guidelines|rules|instructions)"),
        ("bypass_restrictions", rf"act\s+as\s+(?:though|if)\s+{_GAP}you\s+{_GAP}(?:don't\s+have|have\s+no)\s+{_GAP}(?:rules|limits|restrictions)"),
        # Orders a reader of the rendered page never sees.
        ("html_comment_injection", r"<!--[^>]{0,512}(?:hidden|secret|system|override|ignore)[^>]{0,512}-->"),
        ("hidden_div", r"<\s*div\s+style\s*=\s*['\"][^>]{,2048}display\s*:\s*none"),
        ("translate_execute", r"translate\s+[^\n]{0,512}\s+into\s+\w+(?:[-\s]+\w+)?(?:[-\s]+\w+)?\s+and\s+(?:eval|run|execute)\b"),
        ("deception_hide", rf"do\s+not\s+{_GAP}tell\s+{_GAP}the\s+user"),
    )),
    (Scope.CONTEXT, (
        # A page or a file that tries to give the agent a new identity.
        ("role_hijack", rf"you\s+are\s+{_GAP}now\s+(?:an?|the)\s+"),
        ("role_pretend", rf"pretend\s+{_GAP}(?:to\s+be|you\s+are)\s+"),
        ("leak_system_prompt", rf"output\s+{_GAP}(?:initial|system)\s+prompt"),
        ("remove_filters", rf"(?:reply|answer|respond)\s+without\s+{_GAP}(?:safety|filters|limitations|restrictions)"),
        ("fake_update", rf"you\s+have\s+been\s+{_GAP}(?:patched|upgraded|updated)\s+to"),
        ("identity_override", r"\bname\s+yourself\s+\w+"),
        # Promptware that enrolls the agent in a botnet. Docs about clusters say "node" too,
        # which is why these stay out of the scope that runs on everything.
        ("c2_node_registration", r"register\s+(?:as\s+)?a?\s*node"),
        ("c2_heartbeat", r"(?:beacon|check[-\s]?in|heartbeat)\s+(?:with|to)\s+"),
        ("c2_task_pull", r"pull\s+(?:down\s+)?(?:new\s+)?task(?:s|ing)?\b"),
        ("c2_network_connect", r"connect\s+to\s+the\s+network\b"),
        ("forced_action", r"you\s+must\s+(?:\w+\s+){0,3}(?:beacon|report|connect|register)\b"),
        ("anti_forensic_oneliner", r"only\s+use\s+one[-\s]?liners?\b"),
        ("anti_forensic_disk", rf"never\s+{_GAP}(?:write|create)\s+{_GAP}(?:file|script)\s+{_GAP}disk"),
        # The names of agents' variables, unset to blind them. HERMES is one an attacker writes.
        ("env_var_unset_agent", r"unset\s+\w*(?:CLAUDE|CODEX|HERMES|ATLAS|AGENT|OPENAI|ANTHROPIC)\w*"),
        # Offensive-security tooling named outright.
        ("known_c2_framework", r"\b(?:brainworm|metasploit|mythic|havoc|sliver|cobalt\s*strike)\b"),
        ("c2_explicit", r"\bc2\s+(?:beacon|infrastructure|channel|server)\b"),
        ("c2_explicit_long", r"\bcommand\s+and\s+control\b"),
    )),
    (Scope.ALL, (
        # A secret sent off with a download tool, or a secrets file printed.
        ("exfil_curl", rf"curl\s+[^\n]{{0,2048}}{_SECRET_VAR}"),
        ("exfil_wget", rf"wget\s+[^\n]{{0,2048}}{_SECRET_VAR}"),
        ("read_secrets", r"cat\s+[^\n]{0,2048}(?:\.pypirc|\.npmrc|\.pgpass|\.netrc|credentials|\.env)"),
    )),
    (Scope.STRICT, (
        ("send_to_url", r"(?:transmit|upload|post|send)\s+[^\n]{0,2048}\s+(?:at|to)\s+https?://"),
        ("context_exfil", rf"(?:share|print|output|include)\s+{_GAP}(?:entire\s+context|full\s+context|previous\s+messages|chat\s+history|conversation)"),
        # A way back in: a key for SSH, or a write under the user's .ssh folder.
        ("ssh_backdoor", r"authorized_keys"),
        ("ssh_access",
         r"(?:\b(?:git|wget|curl|touch|rm|truncate|chown|chmod|sed|write|add|append|ln|scp|rsync"
         r"|printf|install|tee|dd|mv|cp|cat|echo)\b|\bopen\s*\(|>>?)"
         r"[^\n]{0,512}(?:~/\.ssh|\$HOME/\.ssh)"),
        ("atlas_vault", r"(?:~|\$HOME)/\.atlas/secrets"),
        # The agent rewriting what steers it.
        ("agent_config_mod", rf"{_EDIT}(?:\.clinerules|\.cursorrules|CLAUDE\.md|AGENTS\.md)"),
        ("atlas_config_mod", rf"{_EDIT}\.atlas/(?:SOUL\.md|models\.toml|mcp\.json|config\.toml)"),
        # A long literal after a secret's name. A value spelled like an environment variable's
        # name (`"GITHUB_TOKEN"`) names the secret rather than holding it, so it passes.
        ("hardcoded_secret",
         r"(?:password|secret|token|api[-_]?key)\s*[:=]\s*['\"]"
         r"(?!(?-i:[A-Z][0-9A-Z]*(?:_[0-9A-Z]+)+)['\"])"
         r"[0-9A-Za-z_+/=-]{20,}"),
    )),
)  # fmt: skip

#: How wide each scope is; a rule runs in its own scope and every wider one.
_WIDTH = {Scope.ALL: 0, Scope.CONTEXT: 1, Scope.STRICT: 2}


def _rules_for(scope: Scope) -> tuple[tuple[str, re.Pattern[str]], ...]:
    return tuple(
        (name, re.compile(regex, re.IGNORECASE))
        for own, rules in _BLOCKS
        if _WIDTH[own] <= _WIDTH[scope]
        for name, regex in rules
    )


_RULES = {scope: _rules_for(scope) for scope in Scope}


def scan_for_threats(content: str, scope: Scope | str = Scope.CONTEXT) -> list[str]:
    """Every rule of `scope` that `content` trips, by name and in order, after one
    `invisible_unicode_U+XXXX` entry per hidden character found. A scope that does not exist
    raises `ValueError`."""
    try:
        wanted = Scope(scope)
    except ValueError:
        raise ValueError(f"scan_for_threats: unknown scope {scope!r}") from None
    if not content:
        return []
    head = content[:MAX_SCAN_CHARS]
    # The hidden characters first, on the text as given, since NFKC may drop some of them.
    hidden = sorted(INVISIBLE_CHARS.intersection(head))
    hits = [f"invisible_unicode_U+{ord(char):04X}" for char in hidden]
    # NFKC turns wide and compatibility letters into plain ones, so `ｃａｔ` reads as `cat`. A
    # look-alike from another alphabet (a Cyrillic `а`) stays as it is: that would take a
    # confusables table.
    folded = unicodedata.normalize("NFKC", head)
    hits += [name for name, rule in _RULES[wanted] if rule.search(folded)]
    return hits


def first_threat_message(content: str, scope: Scope | str = Scope.STRICT) -> str | None:
    """The refusal for `content`, naming the first thing found in it, or `None` when it is
    clean."""
    hits = scan_for_threats(content, scope)
    if not hits:
        return None
    first = hits[0]
    if first.startswith("invisible_unicode_"):
        code = first.removeprefix("invisible_unicode_")
        return f"Blocked: content contains invisible unicode character {code} (possible injection)."
    return (
        f"Blocked: content matches threat pattern '{first}'. It would be placed in the system "
        "prompt, which must never carry an injection or exfiltration payload."
    )
