---
title: "What Atlas is"
description: "Atlas is an open-source personal agent with a desktop app, and the place where your coding agents run on every account and every machine."
sidebar:
  order: 1
---

Atlas is an open-source personal agent with a desktop app. Add an API key and you have an assistant that keeps a memory you can read, writes its own skills as it learns how you work, runs routines on a schedule, and talks to you on Telegram.

It is also where your coding agents live. Claude Code, Codex, the Gemini CLI and DeepSeek's harness run inside Atlas as **executors**: the copy you installed, with your settings, your project's instructions and your own login. Atlas shows every session they have, wherever it began.

## What you can do with it

- **Run every coding agent from one window.** Each one you have installed shows up in the Executors tab, with its folders, sessions and plan usage.
- **Use all your accounts at once.** Personal, company A, company B: each account keeps its own folder, settings and history, and they run side by side. When one reaches its plan's limit, the session goes on in the next one. See [Coding agents and accounts](/docs/guides/coding-agents/).
- **Reach your other machines.** Install Atlas on a home lab, a VPS or the office PC and open it from the app on your laptop, over Tailscale or SSH. See [Several machines](/docs/guides/machines/).
- **Hand work to Atlas.** Ask in plain words, and Atlas delegates to the right coding agent, on the account you name, follows along and brings back the result.
- **Let it learn.** Every few messages, a copy of the agent rereads the conversation and keeps what it learned: who you are in your profile, facts about your setup in its notes, how to do a task in a skill.

## How it fits together

Atlas is a server, `atlas serve`, and the clients that talk to it: the desktop app, Telegram, and the app on your other machines. The server runs the agent loop, every tool call passes its approval gate, and it keeps your memory, skills and conversations.

Everything about you lives in one folder, `ATLAS_HOME` (`~/.atlas` unless you choose another): your profile, memory, conversations, settings and the encrypted vault that holds your API keys. Atlas has no account, no telemetry and no cloud of its own; what leaves your machine is what you ask for, such as the calls to your model provider. Your coding agents keep their own logins in their own folders, and Atlas never touches them.

## What it costs

Atlas is free and open source, under the MIT license. You bring an API key from a model provider (OpenAI, Anthropic, Google Gemini or OpenRouter) or a local model through Ollama or vLLM, and you pay that provider for what you use. Your coding agents run on the plans you already have.

## Next

[Install Atlas](/docs/install/): one line on Windows, macOS or Linux, or a container.

Once it is installed, this documentation is also on your machine, as your version ships it: `atlas docs` lists the pages, `atlas docs install` prints one, and `atlas docs --search WORDS` finds a subject. Atlas reads it there too, before it answers a question about itself.
