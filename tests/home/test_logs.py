"""Logs: JSON lines in UTC, one logger per subject, no secret on any line.

Every test configures the real logging of this process with a temporary file and its own stream;
tests/conftest.py puts the previous configuration back afterwards.
"""

import io
import json
import logging
import re
import subprocess
import sys
from pathlib import Path

from atlas.home import REDACTED, Redactor, configure_logging, parse_utc

SRC = Path(__file__).resolve().parents[2] / "src" / "atlas"


def _lines(stream: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def _configured(redactor: Redactor | None = None, **options: Path) -> io.StringIO:
    stream = io.StringIO()
    configure_logging("DEBUG", redactor or Redactor(), stream=stream, **options)
    return stream


# -- the shape of a line -------------------------------------------------------------------------


def test_a_line_is_one_json_object_with_utc_time_level_logger_and_message() -> None:
    stream = _configured()

    logging.getLogger("atlas.memory").info("Indexed %d notes.", 3)

    (line,) = _lines(stream)
    assert line["level"] == "INFO"
    assert line["logger"] == "atlas.memory"
    assert line["msg"] == "Indexed 3 notes."
    assert isinstance(line["ts"], str)
    parse_utc(line["ts"])  # Atlas's UTC format, or this raises


def test_extra_fields_become_fields_of_the_line() -> None:
    stream = _configured()

    logging.getLogger("atlas.jobs").info("Job finished.", extra={"job_id": "j-1", "seconds": 2.5})

    (line,) = _lines(stream)
    assert (line["job_id"], line["seconds"]) == ("j-1", 2.5)


def test_a_line_break_in_a_message_cannot_forge_a_second_line() -> None:
    stream = _configured()

    logging.getLogger("atlas.server").warning('user said: hi\n{"level": "INFO"}\u2028x')

    assert len(stream.getvalue().splitlines()) == 1


def test_an_exception_travels_inside_the_line() -> None:
    stream = _configured()

    try:
        raise ValueError("bad value")
    except ValueError:
        logging.getLogger("atlas.home").exception("It broke.")

    (line,) = _lines(stream)
    assert isinstance(line["exc"], str)
    assert "ValueError: bad value" in line["exc"]


def test_uvicorn_lines_become_json_too_without_the_colored_copy() -> None:
    stream = _configured()

    logging.getLogger("uvicorn.error").info(
        "Started server process", extra={"color_message": "\x1b[32mStarted\x1b[0m"}
    )

    (line,) = _lines(stream)
    assert line["logger"] == "uvicorn.error"
    assert "color_message" not in line


def test_the_file_gets_the_same_lines(tmp_path: Path) -> None:
    log_file = tmp_path / "logs" / "atlas.log"
    _configured(log_file=log_file)

    logging.getLogger("atlas.home").info("Home opened.")

    (line,) = [json.loads(text) for text in log_file.read_text(encoding="utf-8").splitlines()]
    assert line["msg"] == "Home opened."


def test_a_log_file_that_cannot_open_does_not_stop_the_process(tmp_path: Path) -> None:
    (tmp_path / "logs").write_text("a file where the folder should be", encoding="utf-8")
    stream = _configured(log_file=tmp_path / "logs" / "atlas.log")

    logging.getLogger("atlas.home").info("Still logging.")

    first, second = _lines(stream)
    assert first["level"] == "WARNING"
    assert "cannot be opened" in str(first["msg"])
    assert second["msg"] == "Still logging."


# -- no secret on a line -------------------------------------------------------------------------


def test_a_registered_secret_never_reaches_a_line_wherever_it_hides() -> None:
    secret = 'sk-proj-"quoted"\\and\nbroken'
    redactor = Redactor()
    redactor.add(secret)
    stream = _configured(redactor)
    log = logging.getLogger("atlas.secrets")

    log.info("Key is %s.", secret)
    log.info("Calling the provider.", extra={"headers": {"Authorization": f"Bearer {secret}"}})
    try:
        raise RuntimeError(f"provider refused {secret}")
    except RuntimeError:
        log.exception("Call failed.")

    text = stream.getvalue()
    assert "sk-proj" not in text
    assert "quoted" not in text
    assert text.count(REDACTED) >= 3
    assert len(_lines(stream)) == 3


def test_an_age_secret_key_is_removed_even_if_nobody_registered_it() -> None:
    stream = _configured()
    key = "AGE-SECRET-KEY-1" + "Q" * 58

    logging.getLogger("atlas.secrets").info("Loaded %s from the environment.", key)

    assert key not in stream.getvalue()
    assert REDACTED in stream.getvalue()


def test_a_very_short_value_is_not_treated_as_a_secret() -> None:
    redactor = Redactor()
    redactor.add("abc")

    assert redactor.redact("abc") == "abc"


# -- configured only on purpose ------------------------------------------------------------------


def test_importing_every_atlas_module_attaches_no_log_handler() -> None:
    code = (
        "import importlib, logging, pkgutil, sys, atlas\n"
        "for module in pkgutil.walk_packages(atlas.__path__, 'atlas.'):\n"
        "    if module.name.endswith('.windows') and sys.platform != 'win32':\n"
        "        continue\n"
        "    importlib.import_module(module.name)\n"
        "print(len(logging.getLogger().handlers))\n"
    )

    completed = subprocess.run(  # noqa: S603 - this interpreter, fixed code
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )

    assert completed.stdout.strip() == "0"


def test_every_logger_is_named_after_its_subject() -> None:
    checked, wrong = 0, list[str]()
    for source in SRC.rglob("*.py"):
        place = source.relative_to(SRC).parts
        subject = place[0] if len(place) > 1 else "cli"
        text = source.read_text(encoding="utf-8")
        for name in re.findall(r"getLogger\(\s*[\"']([^\"']*)[\"']\s*\)", text):
            checked += 1
            if name != f"atlas.{subject}":
                wrong.append(f"{source.relative_to(SRC)}: {name}")

    assert checked > 0
    assert wrong == []
