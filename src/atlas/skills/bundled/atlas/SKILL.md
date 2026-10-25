---
name: atlas
description: "Questions about Atlas itself: answer from `atlas docs`."
version: 1.0.0
license: MIT
---

# Atlas

Atlas is newer than what you learned in training, and it changes with every update. When someone
asks how Atlas works, how to do something in it, or what one of its settings or commands does,
answer from the documentation of the Atlas installed here, never from memory.

1. `atlas docs` lists the pages, each with a line on what it covers.
2. `atlas docs PAGE` prints the page that fits, such as `atlas docs guides/coding-agents`.
3. Not sure which page: `atlas docs --search WORDS` finds the sections that mention the words, and
   `atlas docs PAGE --search WORDS` searches one page.
4. A question across several subjects: `atlas docs --all` prints every page.

The commands and settings pages (`reference/commands`, `reference/settings`) are written from the
code of this version, so they are exact. Give commands, settings and the names of buttons as the
documentation writes them. When it does not cover the question, say so plainly and say what you
would check, rather than filling the gap with a guess.

How the code works inside is in the repository's architecture,
https://github.com/marqueskaio/atlas/blob/main/docs/ARCHITECTURE.md, which follows the newest
version and may be ahead of this one.
