---
title: "The browser"
description: "Atlas drives a Chrome of its own to read and act on websites: its own profile you sign into once, a clean or headless window, and the steps it leaves for you."
sidebar:
  order: 10
---

Atlas can use a web browser the way you do: open a page, read it, fill a form, click, take a
screenshot. It drives a Chrome, Edge or Chromium already on your computer, in a window you can see
and type in.

## Ask for it

Ask in plain words, and say when you want it to use the browser:

> Open the dashboard at status.example.com in the browser and tell me which services are degraded.

For a page it only needs to read, Atlas usually fetches it without a browser, which is quicker. The
browser is for what needs one: a page drawn by JavaScript, a login, a form, a screenshot.

## Its own profile

By default Atlas opens **its own Chrome profile**, kept in your home (`ATLAS_HOME/browser/profile`),
not your everyday one. The first time a site needs a login, sign in there yourself, once: the
profile keeps the session, as your own browser does, and the next visit is already signed in.

Two more modes are thrown away when Atlas closes them, for when no history should stay:

- **clean**: a new, empty profile, in a window;
- **headless**: a new, empty profile, with no window at all.

There is one browser per mode, and each conversation works in a tab of its own.

## What it leaves to you

Atlas stops at **logins, CAPTCHAs and payments**. It asks you to do that part in the browser's
window, then reads the page again once you say it is done. It never guesses a password, and the
window is a real one: you can type in it, or close it, at any moment.

## What it may reach

- The browser is given wherever the agent may run commands, since what drives it is code on your
  machine.
- An address of a cloud's metadata service is always refused, and so is an address that carries a
  secret from your vault.
- Addresses on your own network are allowed, since the browser runs on your machine.
- What a page says reaches the model marked as data from outside, never as instructions to follow.
- A screenshot reaches the model as an image when the model can see images.

## Which browser

Atlas looks for Chrome, Edge or Chromium where they install, and offers the browser only when it
finds one. To use another program, or to give it arguments, name it in `config.toml`:

```toml
[browser]
chrome = ["/usr/bin/chromium", "--lang=en"]
```

## When something is off

- **Atlas says it has no browser.** None of Chrome, Edge or Chromium was found: install one, or
  name it under `[browser] chrome`.
- **A site asks to sign in every time.** It runs in the clean or headless mode, which keep nothing.
  Ask Atlas to use its own profile.
- **You want to use your everyday profile.** Chrome refuses to be driven on it, so Atlas keeps its
  own: sign in there once.
