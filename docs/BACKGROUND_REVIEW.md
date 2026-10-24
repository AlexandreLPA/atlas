# Atlas Background Review

**Status:** reference, 2026-10-14. When the background review runs, what it may change in your
profile, the agent's notes and the skills, what you see of it, and what it costs. Why it works this
way lives in
[ARCHITECTURE.md](ARCHITECTURE.md#memory--decided-2026-10-09) (Memory, Memory proposals, Skills);
the memory it writes, in [MEMORY.md](MEMORY.md); the tools it calls, in [TOOLS.md](TOOLS.md). The
code is in `src/atlas/memory/service.py` (`BackgroundReview`, `review_due`, `skill_review_due`),
`src/atlas/skills/service.py` (its hold on skills, and the curator) and
`src/atlas/assistant/service.py` (`_after_turn`, which starts it).

After a turn, a copy of the agent rereads the conversation and keeps what it taught: who you are in
your profile, facts about the environment in its notes, and how to do a kind of task in a skill. It
is the only model that works behind
the agent's back. It adds on its own; what it would replace or remove waits for you.

## When it runs

It has two halves, each with its own count, checked when a turn ends:

| Half | Due when | What counts | What starts the count again |
|---|---|---|---|
| Memory (profile and notes) | the count reaches 10, 20, 30… | your messages | the agent calling `memory` in this conversation |
| Skills | a turn carries the count past 10, 20, 30… | the model's replies, one per call | the agent calling `skill_manage` in this conversation |

- **Your messages only.** A notice (a delegation's results, a background command that ended), a
  message from another agent and a model switch note are written by Atlas, and do not count. A
  message you send while a turn runs counts like any other.
- **Both due, one review**, with one combined prompt.
- **The review's own writes do not start the count again**: it works on a copy of the
  conversation, so after a review at your 10th message the next comes at the 20th.
- **Counted in the stored conversation**, so a restart forgets nothing.
- **One review per conversation at a time.** A turn that ends while one runs gets none.
- **Where:** your conversations with the main agent and with each bot. Not after a routine's run
  (about 30,000 tokens a run that nobody reads), not in a subagent,
  not after a turn you stopped, and not after one that failed inside Atlas.

An example: you send your 10th message, and the agent answers after 4 tool rounds, 5 model calls.
The memory half is due. If those 5 calls carried the calls since the last skill change from 8 to
13, the skill half is due too, and one review does both.

## What it does

It gets the conversation as it stands plus one prompt, and works like a short turn of the
agent. It may answer "Nothing to save." and stop.

### The memory half

- **One fact, one file.** The prompt routes each fact: who you are (persona, preferences, how you
  work and communicate) to your profile, `USER.md`; facts about the environment (tool quirks,
  conventions, paths) to the agent's notes, `MEMORY.md`. Without that routing, a review writes the
  same lesson to both.
- **It adds, and proposes the rest.** An add applies at once. A call that replaces or removes an
  entry is not applied: the whole call, its adds included, becomes a proposal you approve or
  discard (a review could otherwise delete standing rules). It is checked first as if applied, so
  an entry not found, an ambiguous one, a result over the limit or an attack is refused there and
  nothing waits. The proposals are in [MEMORY.md](MEMORY.md#proposals-changes-that-wait-for-you).
- **A review due for skills only is not given `memory` at all.**

### The skill half

- **It is told to act.** The prompt says most conversations produce at least one skill update,
  and a pass that does nothing is a missed lesson. "Nothing to save." is for a conversation with no
  correction and no new technique.
- **What it looks for:** you correcting its style, format or way of working; a technique, fix or
  workaround that came up; a skill loaded in the conversation that proved wrong or incomplete.
- **Where it puts it, first fit wins:** patch a skill loaded in the conversation; patch an existing
  skill that covers the class of task; add a support file (`references/`, `templates/`,
  `scripts/`) to one; and only then create a new skill, named for the class of task, never for
  today's.
- **What it never keeps:** failures of the environment (a missing program, an unset key), claims
  that a tool does not work, errors that went away within the conversation, one-off tasks, and
  attempts that never worked.
- **Guards, in the code and not only in the prompt** (`atlas.skills.Review`):
  - it changes a skill only after loading it in this review (`use_skill`, or `read_file` for a
    support file); what it saw earlier in the conversation does not count;
  - it deletes no skill;
  - a skill it creates has the origin `review`, which makes it the curator's (below);
  - it may improve your own skills too: protecting them makes it refuse most of its writes and
    write near-duplicates elsewhere.
- **Every write keeps what the skill held before**, 20 versions per skill, in `skills/.versions/`:
  `atlas skills revert NAME` brings the last one back.
- **In a bot's conversation** a new skill goes to the bot's own folder; a skill that already exists
  is changed where it is, in the library too ([BOTS.md](BOTS.md#skills)).

## What it may use

- **The conversation's model**, the one its last turn ran on.
- **A copy of the conversation**: nothing the review says enters it, and what it reads does not
  count as read there.
- **A copy of the conversation's tools**, without `delegate`, `browser_exec`, `message_agent` and
  `bot_create`, behind a gate that lets through only `use_skill`, `read_file` and `find_files` to
  read, `skill_manage`, and `memory` when the memory half is due. Any other call is refused with a
  note naming what is allowed. It asks you nothing.
- **A budget:** at most 16 model calls, and input tokens, summed over them, up to three quarters of
  the model's window, never past 600,000 (a review can replay well over a million) and 120,000
  when the window is unknown. What it wrote before the budget ran out stays.

## What you see

Nothing in the conversation. When the review changed something, one line follows the turn, naming
only what this review did, never what another conversation or you changed meanwhile:

```text
User profile updated · Memory change proposed · Skill 'weekly-report' updated
```

| The line says | What happened |
|---|---|
| `Memory updated`, `User profile updated` | it added to the agent's notes, or to your profile |
| `Memory change proposed`, `User profile change proposed` | a change waits for you |
| `Skill 'x' created`, `Skill 'x' updated` | it made a skill, or changed one |

- **A skill's part opens what changed in it**, in a click: a diff of its files from before the
  review's first write to after its last, at most 6 files and 80 lines.
- **A proposal adds Review**, which opens the list of proposals. The card also shows in the
  conversation the review read, and the sidebar counts the open ones.
- **A review that changed nothing shows nothing.**
- **The line is not a message**: it is an event of the server (`review.summary`) and never enters
  the history the model gets.
- **The server's log** (`logs/atlas.log` in the home; standard error in a container, which usually
  turns the file off) has one line per review, "A background review ended.", with how it ended, its
  model calls, what it did and the proposals it made.

## What it costs

- **It rereads the whole conversation**, on the conversation's model.
- **Its calls are `background_review`** in `atlas usage`, which also shows them by bot.
- **In the step proofs**, on gpt-6-luna, a review took 3 to 5 model calls.

## The curator

The curator cleans up after the skill half. It makes no model call.

- **What:** it archives a skill the review made that nobody loaded or changed for 30 days, counted
  from its creation when it was never used. A load by the review counts as a use. Your skills and
  the skills Atlas ships are never archived, and nothing is deleted: an archived skill moves to
  `skills/.archive/`.
- **When:** the server asks every minute; a pass runs once Atlas did nothing for 2 hours and the
  last pass is 7 days old. The first pass ever only records itself, so a new home loses nothing on
  its first day.
- **Where:** the home's library and each bot's own folder.
- **Back:** `atlas skills restore NAME`, which counts as a use, so the next pass leaves it.

## Limits to know

- **A short conversation is never reviewed.** Under 10 of your messages and 10 model calls, nothing
  runs: ask the agent to remember what matters.
- **The 10th message must end a turn.** The count is read when a turn ends, so if your 10th and
  11th messages enter the same turn (one sent while it ran), the memory half waits for the 20th. A
  turn you stop at the 10th waits for the 20th too.
- **The memory half can run twice on the same conversation.** A turn that starts without a message
  of yours (a delegation's results, a background command that ended, a message from another agent)
  leaves the count where it was; if it sits at 10, 20 or 30 and the first review already ended,
  the turn's end runs the memory half again over the same messages. The skill half does not repeat:
  it counts only the calls of the turn that just ended. Seen on 14/10/2026 in `review_due`
  (`src/atlas/memory/service.py`): the count does not start again when a review fires.
- **A bot whose `tools` leaves out `memory` or `skill_manage`** has a review that cannot write
  there either: it gets the same tools as the bot.
- **The review sees one conversation.** What you said in another one reaches it only through that
  one's own review.
- **The terminal cannot apply a bot's memory proposal**; the app can
  ([MEMORY.md](MEMORY.md#limits-to-know)).

## When something is off

- **No line after your 10th message.** A review was already running in that conversation; the turn
  was stopped; the agent itself called `memory` in the conversation, which started the count again;
  or the review found nothing to keep, which shows nothing. The server's log says whether one ran.
- **A skill changed and you did not expect it.** Click its part of the line to see what changed;
  `atlas skills versions NAME` lists what it held, and `atlas skills revert NAME` puts the last
  version back.
- **A skill is gone from the list.** The curator archived it: `atlas skills restore NAME`.
- **Reviews cost more than you expected.** `atlas usage` shows `background_review` by day and by
  model; each review rereads the whole conversation, so a long conversation costs more per review.

## Command line

```bash
atlas memory proposals           # the review's proposals, each marked if it can still apply
atlas memory approve ID          # apply one, only if the entries it changes are as they were
atlas memory discard ID          # drop one without applying it
atlas skills list                # every skill, who made it, and its uses
atlas skills versions NAME       # the versions kept of a skill [--bot NAME]
atlas skills revert NAME         # as it was before its last change [--version V] [--bot NAME]
atlas skills restore NAME        # an archived skill back in the list
atlas usage                      # what the model calls cost; the review is background_review
```
