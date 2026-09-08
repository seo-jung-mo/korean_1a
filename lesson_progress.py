"""Local, single-learner lesson history and resumable exercise state."""

import json
import re
import sqlite3
from contextlib import closing


STEP_PREFIXES = (
    "vocab_done", "grammar1_done", "grammar2_done",
    "activity1_completed", "unit_completed",
)
LESSON_KEY = re.compile(
    r"^(?:unit(?:[1-9]|10)_|unit_completed_|vocab_|grammar[12]?_|activity[12]?_|review_)"
)
UNIT_SCOPED_KEY = re.compile(
    r"^(?:unit(?P<direct>10|[1-9])_|unit_completed_(?P<completed>10|[1-9])$|"
    r"(?:vocab|grammar|grammar1|grammar2|activity|activity1|activity2|review)_.+?_(?P<embedded>10|[1-9])(?:_|$))"
)


def is_lesson_key(key):
    return isinstance(key, str) and (
        key == "selected_unit_number" or bool(LESSON_KEY.match(key))
    ) and not any(
        token in key.lower() for token in ("upload", "audio", "drawing", "photo", "file")
    )


def lesson_key_unit(key):
    """Return the owning unit for resumable state, if its key encodes one."""
    if not isinstance(key, str):
        return None
    match = UNIT_SCOPED_KEY.match(key)
    if not match:
        return None
    value = next(value for value in match.groupdict().values() if value is not None)
    return int(value)


def completion_steps(state, history, unit):
    """Past completion is immutable; new completion must be sequential."""
    previous = history.get(unit, 0)
    result = []
    for index, prefix in enumerate(STEP_PREFIXES):
        passed = index < previous or bool(state.get(f"{prefix}_{unit}", False))
        result.append(passed and (not result or result[-1]))
    return tuple(result)


def course_progress(state, history, total_units=10):
    """Return completed steps, total steps, and the 0..1 course ratio."""
    completed = sum(
        sum(completion_steps(state, history, unit))
        for unit in range(1, total_units + 1)
    )
    total = total_units * len(STEP_PREFIXES)
    return completed, total, completed / total if total else 0.0


def unlocked_units(state, history, total_units=10, review_mode=False):
    """Return the contiguous units available to a sequential learner."""
    if review_mode:
        return tuple(range(1, total_units + 1))
    available = [1]
    for unit in range(2, total_units + 1):
        if not completion_steps(state, history, unit - 1)[-1]:
            break
        available.append(unit)
    return tuple(available)


def _encode(value):
    # Preserve tuples used by checked-answer comparisons and integer dict keys.
    if isinstance(value, tuple):
        return {"tuple": [_encode(item) for item in value]}
    if isinstance(value, dict):
        return {"dict": [[_encode(k), _encode(v)] for k, v in value.items()]}
    if isinstance(value, list):
        return [_encode(item) for item in value]
    if value is None or isinstance(value, (bool, str, int, float)):
        return value
    raise TypeError(f"Unsupported lesson state: {type(value).__name__}")


def _decode(value):
    if isinstance(value, dict):
        if "tuple" in value:
            return tuple(_decode(item) for item in value["tuple"])
        return {_decode(k): _decode(v) for k, v in value["dict"]}
    if isinstance(value, list):
        return [_decode(item) for item in value]
    return value


class LessonProgressStore:
    def __init__(self, path):
        self.path = path
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS lesson_completions (
                    unit_number INTEGER PRIMARY KEY CHECK (unit_number BETWEEN 1 AND 10),
                    completed_steps INTEGER NOT NULL CHECK (completed_steps BETWEEN 0 AND 5)
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS lesson_resume (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    state_json TEXT NOT NULL
                )
            """)

    def load(self):
        with closing(sqlite3.connect(self.path)) as connection:
            history = dict(connection.execute(
                "SELECT unit_number, completed_steps FROM lesson_completions"
            ))
            row = connection.execute(
                "SELECT state_json FROM lesson_resume WHERE id = 1"
            ).fetchone()
        return history, _decode(json.loads(row[0])) if row else {}

    def save(self, history, values):
        payload = json.dumps(_encode(values), ensure_ascii=False)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.executemany("""
                INSERT INTO lesson_completions (unit_number, completed_steps) VALUES (?, ?)
                ON CONFLICT(unit_number) DO UPDATE SET
                    completed_steps = MAX(completed_steps, excluded.completed_steps)
            """, history.items())
            connection.execute("""
                INSERT INTO lesson_resume (id, state_json) VALUES (1, ?)
                ON CONFLICT(id) DO UPDATE SET state_json = excluded.state_json
            """, (payload,))
            return dict(connection.execute(
                "SELECT unit_number, completed_steps FROM lesson_completions"
            ))
