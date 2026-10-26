---
title: How a news item is written
date: 2026-10-25
source: Atlas
url: https://example.com/
---

This file is not published: names starting with `_` are skipped. A news item is one Markdown file in
this folder, named after its date and subject (`2026-10-25-a-new-model.md`), with:

- `title`: the story's subject, in our words;
- `date`: the day the story was published;
- `source`: who published it;
- `url`: the full story, where the card links;
- `image` (optional): the story's picture as its publisher serves it, linked and never copied here; without one the page draws its own;
- `kind` (optional): `news` (the default), `model` or `repo`;
- `topics` (optional): a few lowercase words, such as `agents` or `models`; each becomes a tab on the page;
- `atlas` (optional): one sentence on what the story changes for Atlas.

The body is the summary: two or three short paragraphs, written in Atlas's own words, never copied
from the source. The page links to the original for the rest.

## Adding a story from AlphaSignal

1. Find the story and copy its address.
2. Run `npm run news:draft -- <address>` in `site/`. It creates the file with the title, the picture and
   the day the page gives, and leaves a placeholder for the summary.
3. Read the story, then write the summary in your own words, and add `topics` and `kind`. The
   tests fail while the placeholder is still there.
4. Run `npm run check`, open the page with `npm run dev`, and send the item in its own pull request.

The picture is linked from where the publisher serves it, never copied into the repository. If it
stops loading, the page draws its own in its place.
