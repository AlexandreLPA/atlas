# Atlas Bots

**Status:** reference, 2026-10-14. How to make a bot and talk to it, what its definition holds, and
what it shares with the home. Why bots work this way lives in
 [ARCHITECTURE.md](ARCHITECTURE.md#bots--decided-2026-10-13) (Bots); what each tool takes,
in [TOOLS.md](TOOLS.md). The code is in `src/atlas/bots/`, and in `atlas.assistant` for how a bot's
conversation runs.

A bot is a named agent that lives in your Atlas home beside the main agent. It has its own mission,
its own memory and skills, one conversation that never starts over, and its own routines. The main
agent and the bots can write to one another.

## When to make one

Make a bot for a stable role: work with its own goal, tools and sources, way of working, approval
boundary or schedule. A finance bot that checks the bills every Monday is one. A task you need done
once is not: ask the main agent, which can hand it to a subagent.

Keep the roster small. Start with one bot that owns an outcome from end to end, and add specialists
only for roles that stay.

## Make a bot

### In the app

Open the **Bots** tab, beside the conversations, and click **+ New bot**. Three fields are enough:

- **Name**: the bot's handle. What you type becomes a valid one (`Finanças` becomes `@financas`).
- **Title**: how lists call it, such as `Finance`. Up to 60 characters; empty, it is the name.
- **Description**: what the bot helps with, in one line. Up to 160 characters. The other agents
  read it to know when to ask this bot.

**Advanced** opens the rest:

- **Model**: the model its conversation runs on. By default, the home's.
- **Mission and rules**: who the bot is and the rules it keeps, in your words. Left blank, one is
  composed from the title and the description (below).
- **Tools**, **MCP servers** and **Skills of the library**: what the bot may use. By default,
  *Everything the home has*; untick it to choose.

### On the command line

```bash
atlas bot create finance --title "Finance" --description "Bills, due dates and taxes"
atlas bot create writer --title "Writer" --mission-file writer.md --model openai/gpt-6-sol
```

`--mission-file -` reads the mission from the standard input. The command line has no flags for
tools, MCP servers or skills: edit the bot's `BOT.md` (below), or use **Advanced** in the app.

### By asking the main agent

Say it in a conversation with Atlas: "make me a bot that takes care of my bills". The main agent has
a `bot_create` tool for this. It asks you first, with the name and what the bot would be for, and
makes it only once you agree. Bots do not make bots.

### Names

A name has lowercase letters, digits, `-` and `_`, up to 64 characters, and starts with a letter or
a digit. `atlas`, `all`, `everyone` and `user` are taken: `@atlas` is the main agent.

A name never changes: it is the name of the bot's folder, and its conversation is found by it. To
call the bot something else, change its title.

### The mission it is born with

A bot made without a mission gets one composed from its title and description:

```markdown
# Finance

**Role:** Finance
**Mission:** Bills, due dates and taxes

You are Finance, a persistent named agent (`@finance`) in the user's Atlas. You keep your own memory, skills, and conversation history across sessions; the user's profile is shared with the home's other agents.
```

Rewrite it whenever you like. The mission is where the rules that must hold in every conversation
go: "answer in two lines", "never send anything without asking me first".

## Talk to a bot

Click the bot in the **Bots** tab. That always opens the same conversation, the bot's **fixed
conversation**, made the first time you open it. It never starts over, and there is no way to start
another one with the bot; when it fills up, it compacts, as any long conversation does. It lives in
the Bots tab, not among the conversations.

Each row of the tab shows the bot's face, its title and handle, when its conversation last changed
and the last thing said there. The face is drawn from the name: the same name always has the same
face. Above the conversation are whose it is, a button with the bot's routines when it has some,
and **Edit**, which opens its definition.

The conversation's model picker works as in any conversation: a model picked there wins over the
one in the bot's definition.

What other agents and the bot's routines send to it enters this same conversation, each message
saying who sent it, so you can read what the bot was asked and what it answered:

```text
Message from 🤖 Atlas (@atlas): Is October's electricity bill paid?
```

## The definition: `BOT.md`

Each bot is a folder in the home:

```text
~/.atlas/bots/finance/
  BOT.md       its definition: who it is and what it may use
  MEMORY.md    its own memory, once it has written something
  skills/      its own skills
```

`~/.atlas` is the default home; `atlas home show` says where yours is. The bot's conversations and
routines are in the home's database, not in the folder.

`BOT.md` has the format of a Claude Code agent file: a YAML frontmatter, then the mission and rules
in Markdown.

```markdown
---
name: finance
title: Finance
description: Bills, due dates, taxes and the monthly budget
model: openai/gpt-6-sol
tools: [read_file, find_files, web_search, memory, use_skill, skill_manage, conversation_search, cronjob_manage, message_agent]
mcpServers: []
skills: [monthly-report]
---

You keep track of the user's bills, due dates and taxes.

Rules:
- Lead with what is due and when; the details after.
- Never pay, transfer or sign anything. Say what is due, and let the user do it.
```

| Field | What it is | Unset |
|---|---|---|
| `name` | The handle; the same as the folder's name | The folder's name |
| `title` | How lists call the bot, up to 60 characters | The name |
| `description` | What the bot helps with, up to 160 characters | Empty |
| `model` | `provider/model`, such as `openai/gpt-6-sol` | The home's model |
| `tools` | The tools it may use | Every tool the home has |
| `mcpServers` | The MCP servers it may use, by their names in the home's `mcp.json` | Every one |
| `skills` | The library's skills it may see; its own are always seen | The whole library |

- `tools`, `mcpServers` and `skills` take a YAML list or Claude Code's comma-separated text
  (`tools: read_file, memory`). An empty list means none.
- Fields Atlas does not read are ignored.
- The tools a bot may have: `read_file`, `write_file`, `edit_file`, `find_files`, `run_command`,
  `web_search`, `web_fetch`, `browser_exec`, `memory`, `ask_user`, `plan`, `delegate`,
  `use_skill`, `skill_manage`, `conversation_search`, `cronjob_manage` and `message_agent`. The
  app's **Advanced** section lists the ones your home has. When you list them, keep `memory` for
  the bot to remember, `use_skill` for it to use skills, and `message_agent` for it to write to the
  other agents.

**When an edit takes effect.** Atlas reads `BOT.md` from disk at every turn, so you can edit it in
any editor, or with **Edit** in the app. A new mission or model reaches the bot's next turn. Tools
and MCP servers are taken when Atlas opens the conversation, so a conversation already open gets
them the next time Atlas starts.

**A broken definition.** A `BOT.md` that cannot be read (YAML that does not parse, a `name` other
than the folder's) leaves the bot out of the list, with the reason in the log, and a turn in its
conversation ends saying what is wrong. `atlas bot show NAME` prints the reason too.

## Memory

A bot remembers in two places.

- **Your profile, `USER.md`, is one for the whole home.** The main agent and every bot read and
  write the same one. Once one bot saves that you changed jobs, every other bot knows it at its
  next turn, even with its conversation already open; the main agent knows it in its next
  conversation.
- **Its own memory, `MEMORY.md`**, in its folder, holds what it learned of its own work. Only the
  bot writes it. The main agent does not carry it in its instructions, and reads it as a file when
  it needs to.

A bot's instructions carry the profile and its own memory, not the main agent's memory or another
bot's.

The background review runs after a bot's turns as after the main agent's. A change it would make
that replaces or removes an entry becomes a proposal you approve or discard, and the proposal says
whose memory it is for ("Change to the memory of the bot finance"). Approving applies it there.

## Skills

A bot sees its own skills, in its `skills/` folder, and the home's library. The `skills` field
limits which of the library's it sees; its own it always sees, and when both have a skill of the
same name, its own wins.

A skill the bot creates, in a conversation or in the background review, goes to its own folder, so
the library does not fill with skills only one bot uses. A skill that already exists is changed
where it is, in the library too.

`atlas skills list` shows every skill, where it is (the library or a bot) and who made it. To give a
bot's skill to every agent, move it to the library; who made it is kept:

```bash
atlas skills share monthly-report --bot finance
```

`atlas skills versions`, `revert` and `restore` take `--bot NAME` for a bot's skills.

## Routines

A bot's routine runs as the bot, on its schedule. What the run answers enters the bot's fixed
conversation as a message the bot reads and answers there:

```text
[Cronjob "bills" output — scheduled job, not the user. Review it, act on anything that needs action, and summarize for the chat.]
```

A run with nothing to report (`[SILENT]`) and a failed run deliver nothing; the Routines screen and
`atlas routines runs NAME` say how each run ended.

To make one, ask the bot in its conversation ("every Monday at 9, tell me what is due this week").
A bot's `cronjob_manage` makes routines that run as itself, and sees only its own. Or use the
command line:

```bash
atlas routines create bills --bot finance --schedule "every monday 9am" --prompt "Say what is due this week."
```

The main agent lists and changes every routine, the bots' included; the ones it makes are its own.
A command routine (`--command`) runs no agent, so it belongs to no bot. The Routines screen marks a
bot's routines with its handle, and the button above its conversation counts them.

## Agents writing to each other

In a home with bots, the main agent and the bots have a `message_agent` tool (its arguments are in
[TOOLS.md](TOOLS.md), Other agents).

- **The main agent writes to a bot from any conversation you hold with it.** Say "ask finance
  whether the electricity bill is paid": it writes to the bot and ends its turn. The bot answers in
  its own fixed conversation, the answer comes back to yours as a notice, and the main agent tells
  you, naming the bot that answered. The main agent is told to message one bot that clearly
  fits, unless you ask it to consult several.
- **A bot writes to the main agent and to the other bots.** Its message to `@atlas` reaches the
  main agent's conversation that last wrote to it. If the main agent never did, the bot is told to
  say it in its own conversation instead, where you read it.
- **Fire and forget.** Sending returns at once; the answer comes later and starts a turn in the
  sender's conversation. Messages to one bot run in order, one turn at a time.
- **A message from an agent is never yours.** It approves nothing, and does not count as something
  you said.
- **At most 6 messages between agents since you last spoke.** Past that, the tool refuses, and the
  agent stops and tells you what is pending, for you to decide.

Every agent's instructions carry the roster: each other agent's handle, title and description. A
conversation with the main agent keeps the roster of when it started, so after making a bot, start
a new conversation to reach it.

## What a bot can find

`conversation_search` finds, for a bot, only its own conversations. The main agent finds every
conversation, the bots' included, and says whose each one is. What one bot knows of another's work,
it asks with `message_agent`.

## What a bot costs

In a home with bots, `atlas usage` adds a **By bot** section: each bot's turns, its background
reviews and its subagents.

## Delete a bot

In the app: **Edit**, **Delete**, then **Delete it, with its memory and skills?**. On the command
line, `atlas bot delete finance` asks first; `--yes` does not.

Deleting removes the bot's folder (its definition, its memory and its skills) and its routines. Its
conversations are kept, as every conversation is, but no longer belong to the name: a bot made
later with the same name starts a new conversation. A memory proposal for the deleted bot's memory
can only be discarded.

## Homes and backups

Bots belong to one home: `atlas --home work bot list` lists another home's. One server runs every
bot of its home, with their routines and messages. A backup carries the bots; a home made with
`atlas home create --clone` starts with none.

## Limits to know

- **A message between agents can be lost when Atlas stops.** A message waiting and an answer on its
  way live in the running server. A message whose turn already ran stays in the receiver's
  conversation, but its answer is not delivered again.
- **An approval answered "always" holds for the whole home.** A command you always allow in one
  bot's conversation is allowed for every agent.
- **The vault's keys and the MCP servers are the home's.** Limit a bot with `tools` and
  `mcpServers`.
- **Not there yet:** groups where bots talk among themselves (studied, not planned),
  sections, pins and hiding in the list, a face you choose, a voice per bot, bots of other
  machines, and templates to share.

## When something is off

- **A bot is missing from the list.** Its `BOT.md` cannot be read: `atlas bot show NAME` says why.
- **A turn in a bot's conversation fails at once.** The message names what is wrong in its
  `BOT.md`, such as a model not written as `provider/model`.
- **The main agent does not know a new bot.** Start a new conversation: an open one keeps the
  roster of when it started.
- **A change to a bot's tools did not take.** Restart Atlas (close and open the app): tools are
  taken when the conversation opens.

## Command line

```bash
atlas bot list                          # the home's bots: name, title and description
atlas bot create NAME [--title TITLE] [--description TEXT] [--model PROVIDER/MODEL]
                      [--mission-file PATH|-]
atlas bot show NAME                     # its definition, and where it is
atlas bot delete NAME [--yes]           # its folder and routines; its conversations stay
atlas routines create NAME --bot BOT --schedule WHEN --prompt TEXT
atlas skills list                       # every skill, where it is and who made it
atlas skills share NAME --bot BOT       # a bot's skill moved to the library
atlas usage                             # what the model calls cost, by bot too
```
