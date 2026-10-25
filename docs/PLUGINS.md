# Writing a plugin

A plugin is a folder that adds to Atlas. Its format is Claude Code's, so a plugin written for
Claude Code (skills, commands, agents, hooks, MCP servers, programs, options and monitors) runs in
Atlas as it is. What only Atlas reads
sits under `atlas` in the same manifest. The decisions behind it are in
[ARCHITECTURE.md](ARCHITECTURE.md), Plugins.

## Start one

```bash
atlas plugins new my-plugin              # in the home's plugins/, where Atlas finds it
atlas plugins validate ~/.atlas/plugins/my-plugin
```

`new` writes the smallest plugin that works: a manifest, a tool and a skill. `validate` loads it
as Atlas does, in a home of its own that is thrown away after, and says what it registers and what
is wrong. Then turn it on in the app's Plugins screen (or `atlas plugins enable my-plugin`): Atlas
shows what it declares, and it runs only once you consent. A plugin that declares something new
after an update is turned off until you consent again.

A plugin's Python runs inside Atlas with your permissions. There is no sandbox.

## The folder

```text
my-plugin/
├── .claude-plugin/
│   └── plugin.json      # the manifest; `name` is the only field required
├── __init__.py          # optional: register(ctx), for tools, hooks, channels and executors
├── skills/              # optional: one folder per skill, each with a SKILL.md
│   └── my-plugin/
│       └── SKILL.md
├── commands/            # optional: one Markdown file per command
├── agents/              # optional: one Markdown file per subagent
├── hooks/hooks.json     # optional: commands run at the conversation's events
├── monitors/monitors.json  # optional: commands run in the background of each conversation
├── bin/                 # optional: programs the model's commands find by name
└── .mcp.json            # optional: the MCP servers it brings
```

The folder's name is the plugin's name, in kebab-case. A plugin's own files live in its folder;
what it keeps while it runs (caches, `node_modules`, renders) goes in its data folder,
`ctx.data_folder`, which an update does not touch and a backup carries.

## The manifest

```json
{
  "name": "my-plugin",
  "version": "0.1.0",
  "description": "What it adds to Atlas, in one line.",
  "author": { "name": "you" },
  "atlas": {
    "api": 14,
    "tools": ["my_plugin_hello"],
    "secrets": [
      { "name": "MY_SERVICE_TOKEN", "description": "Where to get it.", "url": "https://example.com" }
    ],
    "settings": [{ "name": "api_url", "default": "https://example.com", "description": "Where." }],
    "network": ["example.com"]
  }
}
```

- `api` is the SDK version the plugin is written for. Tools need 14. Atlas refuses a plugin newer
  than itself.
- `tools`, `channels` and `executors` name what its Python registers. Registering anything it
  does not name fails the plugin.
- `secrets` are read from the vault, and only those named (`ctx.secret`). The user stores them on
  the Plugins screen, or with `atlas plugins secret`.
- `settings` are values the user may change, with their defaults (`ctx.setting`).
- `network` lists the hosts it reaches. It is shown at consent and not enforced.
- `hooks` names the events its Python hooks into (needs `api` 15).
- `readers` names the kinds of attachment its Python reads into text, `audio` (needs `api` 21).
- `desktop` names what its screen does outside the app's window (needs `api` 23 and a screen):
  `shortcut`, a key heard in every program, and `paste`, text pasted into the program in focus.

Claude Code's `userConfig` works too: each option is a setting, and a `sensitive` one goes to the
vault as `<PLUGIN>_<KEY>` (`erp` and `apiToken`: `ERP_API_TOKEN`). An MCP server reads an option
as `${user_config.KEY}`; a hook gets each as `CLAUDE_PLUGIN_OPTION_<KEY>`, and an exec-form hook
as `${user_config.KEY}` in its `args`. A monitor's command takes none, as in Claude Code.

## Skills

Each folder of `skills/` with a `SKILL.md` is a skill, in Claude Code's format (YAML frontmatter
with `name` and `description`, then the instructions). While the plugin is on, the model lists it
after your own skills, named after the plugin: `my-plugin:my-plugin`. You can invoke it as
`/my-plugin:my-plugin` too. Atlas never changes a plugin's skill; change it in the plugin.

## Commands

Each Markdown file of `commands/` is a command, Claude Code's older form of a skill: named by its
file (`review.md` is `/my-plugin:review`), its frontmatter optional, its `description` (or its
first line) saying what it does. A skill of the plugin with the same name wins.

## Agents

Each Markdown file of `agents/` is a predefined subagent: a helper for one kind of task, with no
memory of its own. `delegate` offers it by name (`my-plugin:reviewer`), and a task's `agent`
runs it.

```markdown
---
name: reviewer
description: Reviews a change for bugs before it is merged.
tools: Read, Grep, Bash
model: openrouter/anthropic/claude-sonnet-5-5
---
You review changes. Say only what is wrong, with the file and the line.
```

- Its instructions come before the task's context.
- `tools` keeps Atlas's own tools to those named; Claude Code's names work (`Bash` is
  `run_command`, `Read` is `read_file`, `Grep` and `Glob` are `find_files`). Without it, it has
  every tool a subagent has.
- `model` is `provider/model`. Claude Code's aliases (`sonnet`, `inherit`) run on the parent's
  model.

## Hooks

`hooks/hooks.json` is Claude Code's: a command runs at an event, gets the event's JSON on its
stdin, and may stop what happens, change it or add context.

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [{ "type": "command", "command": "python", "args": ["${CLAUDE_PLUGIN_ROOT}/guard.py"] }]
      }
    ]
  }
}
```

- The events Atlas fires: `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`,
  `PostToolUseFailure` and `Stop`. A hook on another of Claude Code's events is read and never
  runs.
- A matcher names Atlas's tool (`run_command`) or Claude Code's (`Bash`).
- The answer is Claude Code's: exit 2 blocks, with stderr as the reason; or JSON on stdout
  (`decision`, `hookSpecificOutput` with `permissionDecision`, `updatedInput`,
  `additionalContext`).
- **`allow` lowers no approval.** `deny` blocks and `ask` makes Atlas ask, but the user's gate
  still decides what `allow` would skip.

Python does the same with `ctx.register_hook(event, run, matcher=...)`: `run` gets the same JSON
as a dict and returns the same answer, or `None`.

## Programs

The files of `bin/` are on the `PATH` of the commands the model runs while the plugin is on, so
the model runs them by name. On Windows the commands run in PowerShell: give a program a `.cmd`
or `.ps1` beside the script bash runs.

## Monitors

`monitors/monitors.json` is Claude Code's: commands that run in the background of each of your
conversations, each line they print reaching the model as a notice, which it answers.

```json
[
  {
    "name": "deploys",
    "command": "\"${CLAUDE_PLUGIN_ROOT}\"/watch-deploys.sh",
    "description": "Deploy status changes",
    "when": "on-skill-invoke:deploy"
  }
]
```

- `when` is `always` (from the conversation's first turn) or `on-skill-invoke:<skill>` (the first
  time that skill is used).
- The lines that come within a second reach the model as one notice. **Each notice is a turn, and
  a turn costs a model call:** print only what the model should act on.
- It stops with the conversation, and when the plugins change.

## MCP servers

`.mcp.json` holds Claude Code's `mcpServers`. Atlas names each server after the plugin
(`plugin_<plugin>_<server>`, its tools `mcp__plugin_<plugin>_<server>__<tool>`). Each server
connects when the plugin is turned on, and stops when it is turned off.

- `${CLAUDE_PLUGIN_ROOT}` is the plugin's folder, and `${CLAUDE_PLUGIN_DATA}` its data folder.
- `${NAME}` is a secret from the vault. Naming it declares it, as `atlas.secrets` does: Atlas
  shows it when you turn the plugin on, so a plugin never reads a key you were not shown. Declare
  it under `atlas.secrets` anyway, to say where to get it.
- `"trust": "untrusted"` makes every call that may write wait for your approval, as in your own
  `mcp.json`.

An MCP server's tools reach the model behind the tool search, like every MCP tool.

## Tools

```python
from typing import Any

from atlas.plugins.sdk import PluginContext, ToolHost


def register(ctx: PluginContext) -> None:
    async def hello(arguments: dict[str, Any], host: ToolHost) -> str:
        host.progress("Saying hello")  # shown under the call, never to the model
        return f"Hello, {arguments['name']}!"  # what the model reads

    ctx.register_tool(
        "my_plugin_hello",
        "Say hello to someone by name.",
        {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
        hello,
        read_only=True,  # it changes nothing, so it may run beside other read-only calls
    )
```

- **Import only `atlas.plugins.sdk` from Atlas.** It is the contract, and it only grows: nothing
  in it is removed or renamed.
- **`register` only registers.** It runs once when the plugin is turned on, with 10 seconds to
  finish. The work starts when the model calls the tool.
- **The model is told the schema, but nothing checks the arguments against it.** Read them with
  care.
- **A tool that raises fails that call,** and the model reads the message. The plugin stays on.
- **When the user stops the turn, the call is cancelled.** End what it started.
- **The approval gate is Atlas's, the same as for its own tools.** A tool named as one of Atlas's
  is left out.
- **`await host.send_file(path)` sends the user a file** (`"api": 22`): Atlas keeps a copy in the
  home, shows it in the conversation, and sends it in the chat when the conversation is a
  channel's. It returns where the home keeps it. A channel sends files with its own
  `send_file(chat, path, media_type)`; one without it says the file waits in the app.

## What stops with it

```python
    studio = start_studio()
    ctx.on_unload(studio.terminate)
```

A stop runs when the plugin is turned off, when Atlas loads it again, and when Atlas closes. It
may be `async`. A stop that raises is logged, and the others still run.

## Readers

A reader turns one kind of attachment into text before the model reads it: the voice plugin
writes down what an audio file says. Declare it under `atlas.readers` (`"api": 21`), and register
it:

```python
from pathlib import Path


def register(ctx: PluginContext) -> None:
    async def read(path: Path, media_type: str) -> str:
        return await my_speech_service(path.read_bytes(), media_type)

    ctx.register_reader("audio", read)
```

- **It reads every such file the user sends,** dropped in the app or sent on a channel, before
  the turn. What it returns goes to the model in the file's note, and Atlas keeps it with the
  file, so each is read once.
- **One reader a kind:** when two plugins read audio, the first loaded reads it, and the other is
  logged.
- **It has 2 minutes.** One that raises, returns nothing or runs out leaves the file's note as it
  was, and the turn goes on.
- `audio` is the only kind for now.

## Two examples

**A business system's MCP, no Python.** The system already has an MCP server, so the plugin only
points at it and teaches its use:

```json
{
  "name": "erp",
  "description": "The company's ERP: orders, stock and invoices.",
  "atlas": {
    "secrets": [{ "name": "ERP_TOKEN", "description": "An API token from the ERP's settings." }]
  }
}
```

```json
{
  "mcpServers": {
    "erp": {
      "type": "http",
      "url": "https://erp.example.com/mcp",
      "headers": { "Authorization": "Bearer ${ERP_TOKEN}" }
    }
  }
}
```

Its `skills/` holds how the company works ("closing the month", "a new order"), so the model
calls the right tools in the right order.

**Video with Remotion.** The plugin brings:

- Remotion's agent skills, to write compositions well;
- a skill that says to hand the composition to Claude Code through `delegate` when Claude Code is
  installed, and to write it directly otherwise;
- a `video_render` tool that runs `npx remotion render` in the project, reports its progress, and
  returns the file;
- a stop that ends the Remotion Studio.

The Studio is a local server, so the model shows it in the side panel with `desktop_preview`.
Remotion is free for individuals and companies of up to three people. A product that makes
videos for its users pays per render.

## Commands with code, terminal commands, your model and your conversations

Version 16 of the SDK (`"api": 16`). Each is named in the manifest:

```json
"atlas": {
  "api": 16,
  "commands": ["status"],
  "cli": ["video-render"],
  "llm": true,
  "notify": true
}
```

```python
def register(ctx: PluginContext) -> None:
    async def status(text: str, host: CommandHost) -> str:
        return "3 renders running."  # the reply, with no model call

    def render(argv: list[str]) -> int:
        print("rendering", argv)  # it writes to the terminal itself
        return 0  # the exit code

    async def summary(text: str, host: CommandHost) -> str:
        said = await ctx.llm.complete(text, system="Two lines at most.")
        return said.text

    ctx.register_command("status", status, "What is rendering.")  # /video:status
    ctx.register_cli_command(
        "video-render", render, "Render from the terminal."
    )  # atlas video-render
    ctx.register_command("summary", summary, "Summarize with your model.")
```

- **A slash command** is typed `/<plugin>:<name>`. What it returns is the reply, and it stays in the
  conversation, so the model reads it at the next turn.
- **A terminal command** gets the arguments as typed. A name Atlas uses is never a plugin's.
- **`ctx.llm.complete`** uses the model your conversations use when they pick none, and each call
  costs your credit: `atlas usage` lists it as `plugin:<name>`. With `json_schema=`,
  `said.parsed` holds the JSON, or `None` when the model did not give JSON that fits.
- **`await ctx.notify(conversation, text)`** brings text to a conversation, where it starts a turn.
  A tool knows its conversation as `host.conversation`, so a render that ends later can say so
  there.

## Model providers

A plugin can add a model provider, written as `models.toml` writes one:

```json
"atlas": {
  "providers": {
    "my-vllm": { "name": "My vLLM", "base_url": "http://192.168.0.5:8000/v1", "key": "MY_VLLM_KEY" }
  }
}
```

It shows in the model picker once its key is in the vault, and leaves when the plugin is turned
off. A plugin never changes a provider Atlas or you already have.

## A screen of its own

`desktop/plugin.js` is the plugin's screen in the app: plain JavaScript (no JSX, no build), loaded
as it is. It imports only `@atlas/plugin-sdk`, `react` and `react/jsx-runtime`.

```javascript
import { host, useValue } from '@atlas/plugin-sdk'
import { useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

function Page({ ctx }) {
  const [renders, setRenders] = useState(null)
  return jsxs('div', {
    children: [
      jsx('button', { onClick: async () => setRenders(await ctx.call('renders')), children: 'Refresh' }),
      jsx('pre', { children: JSON.stringify(renders, null, 2) })
    ]
  })
}

export default {
  id: 'video', // the plugin's name
  register(ctx) {
    ctx.register({ area: 'page', id: 'renders', title: 'Renders', render: () => jsx(Page, { ctx }) })
    ctx.register({ area: 'panel', id: 'now', title: 'Video', render: () => jsx('p', { children: 'Ready.' }) })
  }
}
```

- `area: 'page'` is a page of the main area, with an entry in the sidebar; `area: 'panel'` is a tab
  of the side panel; `area: 'box'` is a button beside the words of the conversation's box, as the
  voice plugin's microphone.
- `host.insertText(text)` puts words after what the conversation's box holds, for the user to read
  and send (a dictation); `host.send(text)` sends a message as the box does, making the
  conversation when none is open.
- With `desktop` declared (`"api": 23`): `ctx.shortcut(id, accelerator, run)` holds a key heard
  in every program while the app is open, named as Electron names keys (`Capslock`, `F9`,
  `Control+Shift+Space`), and resolves `''` or why it cannot be held; `ctx.paste(text)` types the
  text where the app's cursor is when its window has the focus (`app`), else pastes it in the
  program in focus (`pasted`), or leaves it on the clipboard when that program takes no keys
  (`copied`). `ctx.bar({ text, tone })` shows a floating bar that never takes the focus (`tone`:
  `recording`, `working`, `done`, `error`), `null` hides it. The voice plugin's dictation anywhere
  uses all three.
- `ctx.call(name, params)` calls the plugin's Python, which registers it with
  `ctx.register_call(name, run)` and names it under `atlas.calls` (`"api": 17`). It runs where
  Atlas runs, also when the app is on another machine.
- `host.state.conversation`, `host.state.connection` and `host.state.home` are the app's state, read
  with `useValue(...)`; `host.request(method, params)` calls the server as the app does.
- `ctx.onEvent(type, listener)`, `ctx.setInterval`, `ctx.storage` and `ctx.onDispose` are taken
  away with the plugin.
- **The screen has all the app's access.** It is shown at consent.

## While you write it

Save, and Atlas loads it again: its Python, its skills, its hooks and its screen, in the app that
is open. A save that breaks it says why in the Plugins screen; the next save that fixes it runs
again. The Plugins screen's **Reload** does the same on asking. Keep what the plugin writes in its
data folder (`${CLAUDE_PLUGIN_DATA}`), not in its own: a plugin writing in its folder loads itself
again and again.

## Plugins it needs

```json
{
  "name": "video",
  "dependencies": ["fonts", { "name": "render-kit", "version": "~2.1.0" }]
}
```

The version is an npm range (`~2.1.0`, `^2.0`, `>=1.4`). The plugin turns on only after those it
needs, and stops when one of them stops, saying which. They load first.

## Installing one by its address

```bash
atlas plugins install owner/repo                     # GitHub
atlas plugins install https://host/team/plugins.git#video --ref v2.1.0
atlas plugins install ~/code/video                   # a folder on this computer, copied
atlas plugins update video                           # again from where it came
atlas plugins remove video                           # its data stays
```

It arrives off; turn it on with consent, as any other. An update that declares something else
waits for your consent again.

## Publishing it in the marketplace

The marketplace is a public repository, `marqueskaio/atlas-marketplace`: every Atlas lists what it
holds, in the app's Marketplace and with `atlas marketplace list`. An item enters by a pull request
that adds one file, `items/<name>.yaml`, and the maintainer reviews it.

```yaml
name: video            # lowercase letters, digits and dashes; the file is named after it
kind: plugin           # skill, mcp, plugin or executor
title: Video
summary: Cut and render videos with Remotion.
author: your-github-account
license: MIT
repo: your-github-account/atlas-video
sha: 0123456789abcdef0123456789abcdef01234567   # the whole commit that is reviewed
path: plugins/video    # the folder in the repository; leave it out for the root
version: "1.0.0"       # what you call it; the commit is what installs
image: https://raw.githubusercontent.com/your-github-account/atlas-video/<sha>/cover.png
```

- **Pick the smallest kind.** A skill alone is a `skill`, one folder with its `SKILL.md`; one MCP
  server is an `mcp` item; a plugin that registers an executor is an `executor`; anything that
  brings more (skills with a server of its own, hooks, a screen, Python) is a `plugin`. The
  marketplace's CI refuses an item whose code is not the kind it says.
- **A skill says what it needs** in its own frontmatter, and the marketplace checks it on the
  user's machine before installing:

  ```yaml
  metadata:
    requires:
      bins: [ffmpeg]        # programs it runs; said when missing, installed anyway
      mcp: [docs]           # MCP servers it calls: items of the marketplace, brought along
      os: [linux, macos]    # where it works; refused elsewhere
  ```

  Its scripts carry their own libraries: a Python script declares them in its `# /// script`
  header and runs with `uv run`; a Node package runs with `npx` at an exact version. A skill
  brings no secret, since the commands the model runs never see the vault: what needs a key goes
  through an MCP server, or is a plugin.
- **An MCP server** is its `mcp.json` entry under `server`, with each `${NAME}` it reads under
  `secrets` (a name, what it is, the page that makes one). What it runs is pinned: `npx` with
  `name@1.2.3`, `uvx` with `name==1.2.3`, `docker run` with an image by its digest, or a remote
  `https` address. Servers that sign in with OAuth wait until Atlas does.
- **A new version is a new pull request** that moves `sha`; the reviewer reads what changed
  between the two commits. Your code must not fetch and replace itself, nor carry compiled files.
  What it does on the network, in the background or outside its own folder goes in its README.
- **Check it first**, as the CI does:

  ```bash
  atlas marketplace check items/video.yaml          # the file, and its code at its commit
  atlas marketplace check items/video.yaml --load   # and its Python loaded, in a home thrown away
  ```

  `--load` runs the plugin's Python: use it on your own plugin, or where foreign code may run.

## Not yet

Atlas lists a plugin that brings any of these, and refuses to turn it on: LSP servers, output
styles, themes and workflows. A screen has a page and a panel; a status bar, a palette and
themes are not in the app.
