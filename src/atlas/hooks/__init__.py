"""Hooks: commands and Python a plugin runs at the loop's events, in Claude Code's events and JSON
(step 16k)."""

from atlas.hooks.errors import HookConfigError
from atlas.hooks.model import (
    CLAUDE_CODE_NAMES,
    EVENTS,
    OTHER_EVENTS,
    Answer,
    CommandHook,
    parse_hooks,
)
from atlas.hooks.monitors import ConversationMonitors, PluginMonitor
from atlas.hooks.service import (
    ConversationHooks,
    HookCallback,
    Hooks,
    PluginCallback,
    PluginCommand,
)

__all__ = [
    "CLAUDE_CODE_NAMES",
    "EVENTS",
    "OTHER_EVENTS",
    "Answer",
    "CommandHook",
    "ConversationHooks",
    "ConversationMonitors",
    "HookCallback",
    "HookConfigError",
    "Hooks",
    "PluginCallback",
    "PluginCommand",
    "PluginMonitor",
    "parse_hooks",
]
