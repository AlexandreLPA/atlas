"""The outward actions of the terminal programs Atlas ships skills for: what changes something
outside this machine, so the user is asked first. Pure.

`ask` rules, with the lists written for `gh`, `gws` and `gcloud` (ROADMAP, Google e GitHub pelo
terminal). A rule is matched against every part of a compound command, and `sh -c` and a full path
would slip past a prefix; the gate hands these rules the variants the detection already unwraps
(`sh -c`, `$(...)`, `env`, `timeout`, a full path), and the rules also look for the program's name
anywhere in a variant, so PowerShell's `& "C:\\...\\gh.exe"`, `cmd /c` and `powershell -Command`
ask too. Asking once too often (`echo gh pr merge`) is the price.

A rule leans towards asking: a `gws` or `gcloud` command asks unless its method reads, and one it
cannot parse asks.

**Atlas's own decisions go the same way** (step 16): turning a plugin on, storing its secret or
setting, letting someone talk through a channel, installing, updating or removing a plugin by its
address (step 16n) or a marketplace item (step 16s: an MCP server connects as it is installed),
and any command that touches the
home's `plugins/`, `plugin-data/` or `atlas.db`. They are the user's consent, so the agent asks
every time, past the guardian and the ⚡, as for an email; found on 14/10/2026, when `atlas plugins
enable --yes` from the agent would have skipped the consent the Plugins screen asks for.
"""

import re
from collections.abc import Iterator, Sequence

#: Trimmed off a word before it is compared: quotes, brackets and separators glued to it.
_TRIM = "\"'`()[]{};|&,"
#: Words that end one command and start the next.
_SEPARATORS = frozenset({"&&", "||", "|", ";", "&", "|&"})
_SUFFIXES = (".exe", ".cmd", ".bat", ".ps1")
#: A program named with nothing after it, or only these, is being looked at, not run.
_VERSION_FLAGS = frozenset({"--version", "-v"})


def command_word(token: str) -> str:
    """A word as a program name: trimmed, lowercased, the folder and Windows suffix taken off."""
    word = token.strip(_TRIM).lower()
    word = word.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    for suffix in _SUFFIXES:
        if word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def _arguments(tokens: Sequence[str]) -> list[str]:
    """The words after a program's name, up to the end of its command."""
    words: list[str] = []
    for token in tokens:
        if token in _SEPARATORS:
            break
        ends = token.rstrip(")`\"'").endswith((";", "&&", "||", "|"))
        word = token.strip(_TRIM).lower()
        if word:
            words.append(word)
        if ends:
            break
    return words


def _leading(words: Sequence[str]) -> list[str]:
    """The command's words before its first flag."""
    leading: list[str] = []
    for word in words:
        if word.startswith("-"):
            break
        leading.append(word)
    return leading


# -- gh -------------------------------------------------------------------------------------------

#: `gh <group> <verb>` that changes something on GitHub or prints a secret.
_GH_CHANGES: dict[str, frozenset[str]] = {
    "pr": frozenset(
        (
            "merge",
            "revert",
            "update-branch",
            "ready",
            "review",
            "comment",
            "edit",
            "create",
            "close",
            "reopen",
            "lock",
            "unlock",
        )
    ),
    "issue": frozenset(
        {
            "create",
            "close",
            "reopen",
            "comment",
            "edit",
            "delete",
            "transfer",
            "lock",
            "unlock",
            "pin",
            "unpin",
            "develop",
        }
    ),
    "release": frozenset({"create", "delete", "edit", "upload", "delete-asset"}),
    "repo": frozenset(
        {
            "create",
            "delete",
            "edit",
            "archive",
            "unarchive",
            "rename",
            "fork",
            "sync",
            "deploy-key",
            "autolink",
        }
    ),
    "gist": frozenset({"create", "delete", "edit", "rename"}),
    "secret": frozenset({"set", "delete", "remove"}),
    "variable": frozenset({"set", "delete", "remove"}),
    "workflow": frozenset({"run", "enable", "disable"}),
    "run": frozenset({"cancel", "rerun", "delete"}),
    "label": frozenset({"create", "edit", "delete", "clone"}),
    "cache": frozenset({"delete"}),
    "project": frozenset(
        {
            "create",
            "delete",
            "edit",
            "close",
            "copy",
            "link",
            "unlink",
            "mark-template",
            "item-add",
            "item-archive",
            "item-create",
            "item-delete",
            "item-edit",
            "field-create",
            "field-delete",
        }
    ),
    "ssh-key": frozenset({"add", "delete"}),
    "gpg-key": frozenset({"add", "delete"}),
    "codespace": frozenset({"create", "delete", "edit", "stop", "rebuild"}),
    "extension": frozenset({"install", "upgrade"}),
    "auth": frozenset({"token"}),
}
_HTTP_WRITES = frozenset({"post", "put", "patch", "delete"})
#: `gh api` sends a POST when it is given fields and no method.
_GH_API_FIELDS = frozenset({"-f", "--field", "--raw-field", "--input"})


def _gh_api(words: Sequence[str]) -> str | None:
    method: str | None = None
    fields = False
    for index, word in enumerate(words):
        if word in ("-x", "--method") and index + 1 < len(words):
            method = words[index + 1]
        elif word.startswith("--method="):
            method = word.split("=", 1)[1]
        elif word.startswith("-x") and len(word) > 2:
            method = word[2:]
        elif word in _GH_API_FIELDS or word.startswith(("--field=", "--raw-field=", "--input=")):
            fields = True
    if method is not None:
        return f"`gh api` sends a {method.upper()} to GitHub" if method in _HTTP_WRITES else None
    return "`gh api` with fields sends a POST to GitHub" if fields else None


def _gh(words: Sequence[str]) -> str | None:
    leading = _leading(words)
    if not leading:
        return None  # `gh --version`, `gh --help`
    group = leading[0]
    if group == "api":
        return _gh_api(words[1:])
    verbs = _GH_CHANGES.get(group)
    if verbs is None or len(leading) < 2 or leading[1] not in verbs:
        return None
    if group == "auth":
        return "`gh auth token` prints the GitHub token"
    return f"`gh {group} {leading[1]}` changes something on GitHub"


# -- gws ------------------------------------------------------------------------------------------

#: Commands of `gws` itself that call no Google API.
_GWS_LOCAL = frozenset({"schema", "help", "version", "generate-skills", "completion"})
#: API methods that only read (Google's Discovery names, lowercased).
_GWS_READS = frozenset(
    {"get", "list", "search", "query", "export", "getprofile", "instances", "batchget"}
)
#: Helpers (`gws gmail +triage`) that only read.
_GWS_READ_HELPERS = frozenset({"+triage", "+read", "+agenda"})
#: What sends an email, said as such on the approval.
_GWS_SENDS = frozenset({"+send", "+reply", "+reply-all", "+forward", "send"})


def _gws(words: Sequence[str]) -> str | None:
    if any(word in ("--dry-run", "--help", "-h") for word in words):
        return None
    helpers = [word for word in words if word.startswith("+")]
    if helpers:
        helper = helpers[0]
        if helper in _GWS_READ_HELPERS:
            return None
        if helper in _GWS_SENDS:
            return f"`gws {helper}` sends an email"
        return f"`gws {helper}` changes something in Google Workspace"
    leading = _leading(words)
    if not leading:
        if all(word in _VERSION_FLAGS for word in words):
            return None  # `which gws`, `gws --version`
        return "a `gws` command Atlas cannot read may change something in Google Workspace"
    if leading[0] in _GWS_LOCAL:
        return None
    if leading[0] == "auth":
        if len(leading) > 1 and leading[1] == "export":
            return "`gws auth export` prints the Google credentials"
        if len(leading) > 1 and leading[1] == "setup":
            return "`gws auth setup` creates a Google Cloud project and turns its APIs on"
        return None
    method = leading[-1]
    if method in _GWS_READS:
        return None
    if method in _GWS_SENDS:
        return "`gws` sends an email"
    return f"`gws ... {method}` changes something in Google Workspace"


# -- gcloud ---------------------------------------------------------------------------------------


#: Verbs that change something. A gcloud command ends in its verb and then its positional
#: arguments (`gcloud config get-value project`), so the verb is looked for among the leading
#: words, and a write verb anywhere among them asks even beside a read one (`projects delete
#: listing-project`).
_GCLOUD_WRITES = frozenset(
    {
        "create",
        "delete",
        "update",
        "set",
        "unset",
        "add",
        "remove",
        "enable",
        "disable",
        "deploy",
        "apply",
        "patch",
        "move",
        "undelete",
        "reset",
        "start",
        "stop",
        "resize",
        "import",
        "export",
        "cancel",
        "run",
        "submit",
        "attach",
        "detach",
        "rollback",
        "login",
        "activate-service-account",
        "init",
        "add-iam-policy-binding",
        "remove-iam-policy-binding",
        "set-iam-policy",
    }
)


def _gcloud_reads(word: str) -> bool:
    return word.startswith(("list", "describe", "get")) or word in (
        "read",
        "search",
        "info",
        "version",
        "help",
        "tail",
    )


def _gcloud(words: Sequence[str]) -> str | None:
    if any(word in ("--help", "-h", "help") for word in words):
        return None
    leading = _leading(words)
    if not leading:
        if all(word in _VERSION_FLAGS for word in words):
            return None  # `which gcloud`, `gcloud --version`
        return "a `gcloud` command Atlas cannot read may change something on Google Cloud"
    tokens = [word for word in leading if word in ("print-access-token", "print-identity-token")]
    if tokens:
        return f"`gcloud {tokens[0]}` prints a Google Cloud token"
    if not any(word in _GCLOUD_WRITES for word in leading) and any(map(_gcloud_reads, leading)):
        return None
    return f"`gcloud {' '.join(leading)}` may change something on Google Cloud"


_PROGRAMS = {"gh": _gh, "gws": _gws, "gcloud": _gcloud}

# -- atlas ----------------------------------------------------------------------------------------

#: `atlas <group> <verb>` that the user decides: a plugin on, its secrets and settings, a new one
#: in the home's plugins folder (step 16j), a plugin fetched, updated or taken away by its address
#: (step 16n), who may talk through a channel, and what comes from the marketplace (step 16s),
#: which may start a server or change a skill the model reads.
_USER_DECIDES: dict[str, frozenset[str]] = {
    "marketplace": frozenset(("install", "update", "remove")),
    "pairing": frozenset(("forget", "approve")),
    "plugins": frozenset(
        ("new", "set", "secret", "disable", "enable", "install", "update", "remove")
    ),
}
#: The home's entries that hold the plugins' code and data and what the user decided (the
#: database keeps the consents, the approvals kept for good and who may talk), in any spelling
#: the gate folds the home into (`~/.atlas/...`) or a Windows path.
_ATLAS_FILES = re.compile(
    r"[\\/]\.atlas[\\/](?:plugins|plugin-data|atlas\.db)(?![\w.-])", re.IGNORECASE
)


def _atlas(words: Sequence[str]) -> str | None:
    rest = list(words)
    while rest and rest[0].startswith("--home"):
        rest = rest[1:] if "=" in rest[0] else rest[2:]
    leading = _leading(rest)
    if len(leading) >= 2 and leading[1] in _USER_DECIDES.get(leading[0], frozenset()):
        return f"`atlas {leading[0]} {leading[1]}` is the user's own decision"
    return None


def outward_actions(command: str) -> Iterator[tuple[str, str]]:
    """Each outward action in one spelling of a command: the program's command as the rules read
    it (`gh pr merge 1`, lowercased), and why it changes something outside this machine. Nothing
    for a command that only reads."""
    if _ATLAS_FILES.search(command):
        yield "atlas files", "it touches Atlas's plugins or its database, which the user decides"
    tokens = command.split()
    for index, token in enumerate(tokens):
        name = command_word(token)
        rule = _atlas if name == "atlas" else _PROGRAMS.get(name)
        if rule is None:
            continue
        words = _arguments(tokens[index + 1 :])
        reason = rule(words)
        if reason is not None:
            yield " ".join((name, *words)), reason


def outside_program(command: str) -> str | None:
    """The program among `gh`, `gws` and `gcloud` that a command runs, whose output brings text
    from outside (an email, an issue's comments) and is read as data, not instructions; `None`
    when it runs none of them. Otherwise an email read through the Google skill would reach the
    model as plain command output."""
    for token in command.split():
        name = command_word(token)
        if name in _PROGRAMS:
            return name
    return None


#: The flags of `gws` that name an email's recipients.
_RECIPIENT_FLAGS = frozenset({"--to", "--cc", "--bcc"})


def email_recipients(command: str) -> list[str]:
    """The addresses a `gws` command sends an email to (`--to`, `--cc`, `--bcc`), lowercased, in
    order and without repeats; empty for anything else, and for a reply, whose recipients are the
    thread's."""
    found: dict[str, None] = {}
    tokens = command.split()
    for index, token in enumerate(tokens):
        if command_word(token) != "gws":
            continue
        words = _arguments(tokens[index + 1 :])
        if _gws(words) is None:
            continue
        for position, word in enumerate(words):
            flag, _, value = word.partition("=")
            if flag not in _RECIPIENT_FLAGS:
                continue
            if not value and position + 1 < len(words):
                value = words[position + 1]
            for address in value.split(","):
                address = address.strip(_TRIM + "<>")
                if "@" in address:
                    found[address] = None
    return list(found)
