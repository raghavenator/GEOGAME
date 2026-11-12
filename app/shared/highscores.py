"""SQLite-backed best scores, keyed by game and settings configuration."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from flask import Flask, current_app, g

SCHEMA = """
CREATE TABLE IF NOT EXISTS high_scores (
    game_id      TEXT    NOT NULL,
    settings_key TEXT    NOT NULL,
    best_score   INTEGER NOT NULL,
    max_score    INTEGER NOT NULL,
    plays        INTEGER NOT NULL DEFAULT 0,
    updated_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (game_id, settings_key)
);
"""


@dataclass(frozen=True)
class RecordResult:
    best: int
    is_new_best: bool
    previous_best: int | None


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
    return g.db


def close_db(_exc: BaseException | None = None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_app(app: Flask) -> None:
    with sqlite3.connect(app.config["DATABASE"]) as conn:
        conn.executescript(SCHEMA)
    app.teardown_appcontext(close_db)


def get_best(game_id: str, key: str) -> int | None:
    row = get_db().execute(
        "SELECT best_score FROM high_scores WHERE game_id = ? AND settings_key = ?", (game_id, key)
    ).fetchone()
    return None if row is None else int(row["best_score"])


def record_score(game_id: str, key: str, score: int, max_score: int) -> RecordResult:
    db = get_db()
    previous = get_best(game_id, key)
    is_new = previous is None or score > previous
    db.execute(
        """
        INSERT INTO high_scores (game_id, settings_key, best_score, max_score, plays)
        VALUES (?, ?, ?, ?, 1)
        ON CONFLICT (game_id, settings_key) DO UPDATE SET
            best_score = MAX(best_score, excluded.best_score),
            plays = plays + 1,
            updated_at = datetime('now')
        """,
        (game_id, key, score, max_score),
    )
    db.commit()
    return RecordResult(best=max(score, previous or 0), is_new_best=is_new, previous_best=previous)
