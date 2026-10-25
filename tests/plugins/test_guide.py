"""The plugin guide, the documentation's `extending/plugins` page, kept whole: every name of the SDK
said in it, and the copy the write-plugin skill reads the same as the guide."""

import inspect
import re
from typing import Protocol

from atlas.docs import PAGES_FOLDER
from atlas.plugins import sdk
from atlas.skills import BUNDLED_FOLDER

GUIDE = PAGES_FOLDER / "extending" / "plugins.md"


def _said() -> set[str]:
    """Every word inside the guide's code, inline or fenced."""
    text = GUIDE.read_text(encoding="utf-8")
    spans = re.findall(r"```.*?```|`[^`\n]+`", text, flags=re.DOTALL)
    return {word for span in spans for word in re.findall(r"[A-Za-z_]\w*", span)}


def _sdk_names() -> set[str]:
    """The SDK's classes, and each member of its protocols, both Atlas's and a plugin's."""
    names = {"API_VERSION"}
    for name, value in vars(sdk).items():
        if name.startswith("_") or not inspect.isclass(value):
            continue
        if value.__module__ != sdk.__name__:
            continue
        names.add(name)
        if Protocol in getattr(value, "__mro__", ()) or getattr(value, "_is_protocol", False):
            members = {*vars(value), *getattr(value, "__annotations__", {})}
            names |= {member for member in members if not member.startswith("_")}
    return names


def test_the_guide_names_everything_the_sdk_offers() -> None:
    missing = sorted(_sdk_names() - _said())

    assert missing == [], f"The plugin guide does not name: {', '.join(missing)}"


def test_the_skill_that_writes_plugins_reads_the_guide_as_it_is() -> None:
    copy = BUNDLED_FOLDER / "write-plugin" / "references" / "guide.md"

    assert copy.read_text(encoding="utf-8") == GUIDE.read_text(encoding="utf-8"), (
        "Copy src/atlas/docs/pages/extending/plugins.md to "
        "src/atlas/skills/bundled/write-plugin/references/guide.md."
    )
