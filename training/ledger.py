import sqlite3
from dataclasses import dataclass
from fractions import Fraction

from station import ensure_table
from weighing.units import decode_grams, encode_grams


_LEDGER = '''CREATE TABLE training_feedback (
    feedback_id TEXT NOT NULL PRIMARY KEY,
    estimate_g TEXT NOT NULL,
    actual_g TEXT NOT NULL,
    cut_id INTEGER NOT NULL UNIQUE REFERENCES model_cuts (cut_id) CHECK (cut_id > 0)
)'''


@dataclass(frozen=True, slots=True)
class FeedbackRecord:
    feedback_id: str
    estimate_g: Fraction
    actual_g: Fraction
    cut_id: int


def initialize_feedback_ledger(db: sqlite3.Connection) -> None:
    ensure_table(db, 'training_feedback', _LEDGER)


def find_feedback(db: sqlite3.Connection, feedback_id: str) -> FeedbackRecord | None:
    row = db.execute(
        'SELECT * FROM training_feedback WHERE feedback_id = ?', (feedback_id,)
    ).fetchone()
    if row is None:
        return None
    return FeedbackRecord(
        row['feedback_id'], decode_grams(row['estimate_g']),
        decode_grams(row['actual_g']), row['cut_id'],
    )


def append_feedback(db: sqlite3.Connection, record: FeedbackRecord) -> None:
    db.execute(
        '''INSERT INTO training_feedback (feedback_id, estimate_g, actual_g, cut_id)
           VALUES (?, ?, ?, ?)''',
        (
            record.feedback_id, encode_grams(record.estimate_g),
            encode_grams(record.actual_g), record.cut_id,
        ),
    )
