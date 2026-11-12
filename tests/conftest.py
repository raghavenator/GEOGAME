from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app import create_app


@pytest.fixture()
def app(tmp_path: Path) -> Iterator[Flask]:
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "test.sqlite3"), "FLAG_REVEAL_SEED": 1234})
    yield app


@pytest.fixture()
def client(app: Flask) -> FlaskClient:
    return app.test_client()
