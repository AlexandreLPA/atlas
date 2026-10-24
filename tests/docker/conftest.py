"""The image's tests run against a built image named in `ATLAS_TEST_IMAGE`
(docs/ARCHITECTURE.md, Container and Tests). CI's docker job builds it first;
by hand:

    docker build -t atlas:local .
    ATLAS_TEST_IMAGE=atlas:local uv run pytest tests/docker --no-cov

Without the variable they are skipped: the image is a Linux one, and building it in every run of
the other tests would cost minutes.
"""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from docker_support import Stack

IMAGE = os.environ.get("ATLAS_TEST_IMAGE", "").strip()
HERE = Path(__file__).resolve().parent


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    if IMAGE:
        return
    skip = pytest.mark.skip(reason="ATLAS_TEST_IMAGE names no built image")
    for item in items:
        if HERE in item.path.parents:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def stack() -> Iterator[Stack]:
    """One compose project for the session: Atlas's container, empty, and the fake model."""
    made = Stack(IMAGE)
    try:
        made.up()
        yield made
    finally:
        made.down()
