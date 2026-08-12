"""Shared fixtures. Every test gets its own throwaway database file."""

import pytest

import db


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "arena.db"


@pytest.fixture
def conn(db_path):
    connection = db.connect(db_path)
    db.init_db(connection)
    yield connection
    connection.close()
