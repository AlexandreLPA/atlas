"""The health route is what Docker, the installer and the desktop app use to know Atlas is up."""

from starlette.testclient import TestClient

from atlas import __version__
from atlas.server import PROTOCOL, create_app


def test_health_reports_ok_the_running_version_and_the_protocol() -> None:
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__, "protocol": PROTOCOL}


def test_health_rejects_writes() -> None:
    client = TestClient(create_app())

    response = client.post("/health")

    assert response.status_code == 405
