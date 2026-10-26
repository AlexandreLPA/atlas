---
title: "Voice and dictation"
description: "Dictate into any program on your computer with one key, send Atlas audio, hear its answers, and talk to it live, all through OpenAI on your own key."
sidebar:
  order: 9
---

The Voice plugin, which ships with Atlas, lets you speak to Atlas and to the rest of your computer.
Everything goes through OpenAI, on the `OPENAI_API_KEY` Atlas's OpenAI provider already uses.

## Turn it on

Open **Settings**, then **Plugins**, and choose **Turn on…** beside Voice. Atlas shows what it may
do (reach `api.openai.com`, read `OPENAI_API_KEY`, hold a key on your keyboard and paste where you
are) and turns it on once you agree. With no OpenAI key yet, add one first
([Models and keys](/docs/guides/models/)).

## Dictate anywhere

Press **Caps Lock** in any program to start recording, and press it again: the words land where your
cursor is. A small bar above the taskbar shows that Atlas is listening, without taking the focus.

- **In Atlas's own window**, the words are typed into the message box or an executor's terminal,
  and the clipboard is not touched.
- **In another program**, the words go on the clipboard, Atlas presses the paste key for you, and a
  moment later the clipboard gets back what it held.
- **A program running as administrator** on Windows refuses keys from an ordinary program, so there
  the words stay on the clipboard and the bar says to paste them.
- A recording shorter than half a second is dropped, and one stops by itself at 10 minutes.

The key is yours to change: the **Dictations** page, in the sidebar, has **Change the key**
(a key or a combination such as `F9` or `Control+Shift+Space`) and **Turn off**. Caps Lock keeps
its capitals as they were: Atlas presses it once more for you. Pasting uses the system's own keys:
on a Mac through `osascript`, on Linux through `xdotool` or `wtype`, which must be installed there.

## The microphone in the message box

The microphone button beside the message box dictates too: the words land in the box, unsent, for
you to read and send.

## Your dictations

Every dictation is kept in your home, with its audio, until you delete it: the **Dictations** page
lists them, plays, copies and deletes them. A recording that could not be written down is kept
with why, and **Write it down again** tries once more. A backup carries them.

## Send audio

An audio file you attach in the app, or a voice note you send on [Telegram](/docs/guides/telegram/),
is written down before Atlas reads it: the model receives the words, with a note that a speech model
wrote them and may have misheard one. OGG, MP3, M4A, WAV and WebM work, up to 25 MB.

## Hear the answer

Atlas answers in writing, even to audio. Ask to hear an answer ("say it", "send me that as audio")
and it sends a spoken one: in the app as a file you play, on Telegram as a voice message.

## Talk live

**Talk by voice**, the button beside the message box, opens a live voice conversation: you speak, the voice
answers, and you can interrupt it. Anything that needs work, facts or memory goes to Atlas as a
turn of the conversation, which you see on screen, and the voice says the answer when it comes.

It ends with its button, when you say "stop" or "bye" alone, or after two minutes of silence. A
request Atlas is still working on is never cut for silence.

## What it costs

You pay OpenAI on your key, at OpenAI's prices (these were checked in October 2026):

| | Model | Price |
|---|---|---|
| Writing speech down | `gpt-transcribe` | about US$ 0.0045 a minute |
| Speaking an answer | `gpt-4o-mini-tts` | OpenAI's speech price, for the answers you ask to hear |
| Talking live | `gpt-live-1` | about US$ 0.05 a minute, silence included |

What the live conversation hands over to Atlas runs on the plugin's own model (GPT-6 Luna with no
reasoning, by default) and is priced as any turn.

## Its settings

Under **Settings**, then **Plugins**, then Voice:

| Setting | Default | What it does |
|---|---|---|
| `dictation_shortcut` | `Capslock` | The key that dictates anywhere; empty turns it off |
| `transcription_model` | `gpt-transcribe` | The model that writes down what you say |
| `speech_model` | `gpt-4o-mini-tts` | The model that speaks Atlas's answers |
| `live_model` | `gpt-live-1` | The model of the live conversation |
| `voice` | `marin` | The voice that speaks |
| `voice_turns_model` | `openai/gpt-6-luna` | The model that answers in the live conversation; `conversation` for the conversation's own |
| `quiet_close_seconds` | `120` | Seconds of silence before the live conversation closes; `0` never |
