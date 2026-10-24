"""ask_user: questions to the user (decided on 09/10/2026).

- **One to five independent questions per call**, each with up to four choices (the recommended
  one first, marked by Atlas), several choices at once (`multi_select`), or a free answer (no
  choices). The screen adds an "Other" row for a typed answer.
- **The turn waits for the answer**, from the app, the phone or a channel: whoever delivers the
  questions is an `Asker`, given by the server (step 8 of the roadmap). A turn with nobody to
  answer, such as a scheduled one, is not given this tool.
- **The answer says what happened**: each question answered, skipped or unanswered, and the wait
  submitted, cancelled, timed out or undelivered, with a notice saying why.
- **Not for approving a dangerous action**: that is the trust subject's (step 7).
- **Always on**: deferred behind tool search, it is used far less; asking must be at hand.
"""

import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, cast

from atlas.agent import Tool

MAX_QUESTIONS = 5
#: The screen always adds an "Other" row for a typed answer.
MAX_CHOICES = 4
MAX_CHOICE_CHARS = 8_000
RECOMMENDED = "(Recommended)"


@dataclass(frozen=True, slots=True)
class Question:
    """One question as the user sees it."""

    #: The question's place in the call (`q0`, `q1`…), which the answers are keyed by.
    id: str
    text: str
    #: The choices shown, the first marked recommended; empty for a free answer.
    choices: tuple[str, ...] = ()
    multi_select: bool = False


class Outcome(StrEnum):
    """How the wait for the answers ended."""

    SUBMITTED = "submitted"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    UNDELIVERED = "undelivered"


@dataclass(frozen=True, slots=True)
class Answers:
    """What came back: an answer per question id (a choice, the choices of a multiple choice,
    typed text, or `None` for a question left blank), and how the wait ended."""

    outcome: Outcome
    answers: Mapping[str, str | list[str] | None] = field(
        default_factory=dict[str, str | list[str] | None]
    )
    #: Why the wait ended without a submit, in words for the model.
    notice: str | None = None


#: Delivers the questions to the user and waits for the answers.
type Asker = Callable[[tuple[Question, ...]], Awaitable[Answers]]

_SHAPE = 'Pass questions=[{"question": ..., "choices": [...]}]; one question is a one-entry list.'


def ask_user(asker: Asker) -> Tool:
    async def run(arguments: dict[str, Any]) -> str:
        questions = questions_from(arguments.get("questions"))
        if isinstance(questions, str):
            return f"[Atlas: {questions}]"
        answers = await asker(questions)
        return result(questions, answers)

    return Tool(
        name="ask_user",
        description=(
            "Asks the user one or more questions when you need a decision, a clarification or "
            f"feedback before going on. Put every independent question in one call (1 to "
            f"{MAX_QUESTIONS}). Each takes up to {MAX_CHOICES} choices, your recommended one "
            "first; multi_select for several; no choices for a free answer. The user can always "
            "type another answer. Choices go only in `choices`, never in the question text. "
            "The result gives each answer and how the wait ended (submitted, cancelled, "
            "timed_out, undelivered). Decide low-stakes questions yourself."
        ),
        parameters={
            "type": "object",
            "properties": {
                "questions": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": MAX_QUESTIONS,
                    "items": {
                        "type": "object",
                        "properties": {
                            "question": {"type": "string"},
                            "choices": {
                                "type": "array",
                                "items": {"type": "string"},
                                "maxItems": MAX_CHOICES,
                            },
                            "multi_select": {"type": "boolean"},
                        },
                        "required": ["question"],
                    },
                },
            },
            "required": ["questions"],
            "additionalProperties": False,
        },
        run=run,
    )


def questions_from(raw: object) -> tuple[Question, ...] | str:
    """The questions to show, or what is wrong with them."""
    if not isinstance(raw, list) or not raw:
        return f"questions must be a non-empty list. {_SHAPE}"
    entries = cast("list[Any]", raw)
    if len(entries) > MAX_QUESTIONS:
        return f"ask at most {MAX_QUESTIONS} questions in one call."
    questions: list[Question] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            return f"questions[{index}] must be an object. {_SHAPE}"
        fields = cast("dict[str, Any]", entry)
        text = str(fields.get("question") or "").strip()
        if not text:
            return f"questions[{index}].question is empty."
        offered = fields.get("choices")
        choices: list[str] = []
        if offered is not None:
            if not isinstance(offered, list) or not all(
                isinstance(choice, str) for choice in cast("list[Any]", offered)
            ):
                return f"questions[{index}].choices must be a list of texts."
            given = cast("list[str]", offered)
            for position, choice in enumerate(given):
                if len(choice.strip()) > MAX_CHOICE_CHARS:
                    return (
                        f"questions[{index}].choices[{position}] has {len(choice.strip()):,} "
                        f"characters, past the {MAX_CHOICE_CHARS:,} allowed. Move the detail "
                        "into the question."
                    )
            choices = [choice.strip() for choice in given if choice.strip()][:MAX_CHOICES]
            if given and not choices:
                return (
                    f"questions[{index}].choices are all blank. Send real choices, or none for "
                    "a free answer."
                )
        if len(choices) > 1:
            choices[0] = f"{choices[0]} {RECOMMENDED}"
        questions.append(
            Question(
                id=f"q{index}",
                text=text,
                choices=tuple(choices),
                multi_select=fields.get("multi_select") is True and bool(choices),
            )
        )
    return tuple(questions)


def result(questions: tuple[Question, ...], answers: Answers) -> str:
    """The answers for the model, in question order, the questions echoed."""
    responses: list[dict[str, Any]] = []
    for question in questions:
        raw = answers.answers.get(question.id)
        value = _clean(raw, multi=question.multi_select) if raw not in (None, "", []) else None
        if value:
            status = "answered"
        else:
            status, value = ("skipped" if question.id in answers.answers else "unanswered"), None
        responses.append(
            {
                "question": question.text,
                "choices_offered": [_plain(choice) for choice in question.choices] or None,
                "status": status,
                "user_response": value,
            }
        )
    said: dict[str, Any] = {"responses": responses, "outcome": answers.outcome.value}
    if answers.notice:
        said["notice"] = answers.notice
    return json.dumps(said, ensure_ascii=False)


def _clean(raw: str | list[str] | None, *, multi: bool) -> str | list[str] | None:
    """The answer without the screen's marks: a multiple choice is a list."""
    if raw is None:
        return None
    if not multi:
        return _plain(raw if isinstance(raw, str) else ", ".join(raw))
    items = raw if isinstance(raw, list) else [raw]
    return [_plain(item) for item in items if item.strip()]


def _plain(text: str) -> str:
    stripped = text.strip()
    if stripped.casefold().endswith(RECOMMENDED.casefold()):
        return stripped[: -len(RECOMMENDED)].strip()
    return stripped
