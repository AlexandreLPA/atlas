---
title: "Backups, updates and homes"
description: "Back up everything Atlas knows in one zip without the keys, restore it on another machine, keep separate homes for work and personal life, and update Atlas from the app."
sidebar:
  order: 14
---

Everything Atlas knows about you lives in one folder, its **home**: `~/.atlas` unless you choose
another. This page is about keeping that folder safe, moving it, splitting it, and keeping Atlas
itself up to date.

## Back up

In the app, click the home's name at the foot of the sidebar, open its dialog and choose
**Back up**: pick where the zip goes. From a terminal:

```bash
atlas backup
```

writes `~/atlas-backup-<home>-<time>.zip` and keeps the last 3. `atlas backup -o FILE` writes it
where you say, and `-o -` to standard output, which is how a backup leaves a container.

A backup holds your settings, your profile and memory, the skills, the conversations, the routines,
your plugins with their data, and the dictations. It never holds your keys: they belong to the
machine, encrypted for it. Each backup is checked once written, every file in it and the database's
integrity.

To back up every night, make it a [routine](/docs/guides/routines/):

```bash
atlas routines create backup --schedule "every day at 3am" --command "atlas backup"
```

## Restore

- **Into a new home**, from the app: the home's dialog has **Import**, which checks the whole zip,
  shows what it holds, and makes a new home from it, leaving the one open as it is.
- **Over a home**, from a terminal: `atlas restore FILE`. The whole archive is checked before
  anything is touched, a home that already has data asks first, and a backup with fewer
  conversations than the home it replaces says so.

After a restore on another machine, store your keys again: the app asks for one when it opens.

Before it changes its database for a new version, Atlas also keeps a backup of it on its own, in the
home's `backups/` folder, the last 5.

## Several homes

A **home** is a whole Atlas: its own settings, keys, memory, skills and conversations. Keep one for
work and one for personal life, and what one learns never reaches the other.

- **In the app**, the home's name at the foot of the sidebar opens a menu with the other homes and
  **New home**, which starts empty or as a copy of another: its settings, models, MCP servers, keys,
  profile, memory and skills, with no conversations.
- **Switching** keeps the other home running, so a turn there goes on and tells you when it ends.
- **From a terminal**: `atlas home list`, `atlas home create NAME --clone [--from HOME]`,
  `atlas home use NAME` (the default for the terminal), and `atlas --home NAME COMMAND` for one
  command.

The default home is `~/.atlas`; the others live in `~/.atlas/homes/NAME/`. The variable
`ATLAS_HOME` names any home by its folder. Only one Atlas serves a home at a time, so two agents
never write the same memory.

## Update Atlas

When a new version is out, a button with the number of changes shows at the foot of the sidebar,
above the home. It lists what changed and what closing now would stop, then **Update and reopen**:
the app closes, Atlas updates, and the app opens again on the new version, saying how it went.
**Settings**, then **General**, has **Look for an update** too.

From a terminal, with the app closed:

```bash
atlas update
```

or run the install line again. If any step of an update fails, Atlas goes back to the version it
was on. An installation with changes of its own made by hand is refused rather than overwritten.
Every change is listed in the [changelog](/changelog/).

## Where the home is

| In the home | What |
|---|---|
| `config.toml`, `models.toml` | your settings and your models |
| `atlas.db` | conversations, routines, memory proposals, skills' usage |
| `SOUL.md`, `memory/` | who Atlas is, your profile and its notes |
| `skills/` | the skills, with their versions and archive |
| `plugins/`, `plugin-data/` | the plugins you added, and every plugin's data |
| `secrets/` | the encrypted vault and its key: never in a backup |
| `backups/` | the database's copies before each migration |
| `homes/` | the other homes of this machine |
