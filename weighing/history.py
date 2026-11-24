import sqlite3
from dataclasses import dataclass
from fractions import Fraction

from weighing.units import decode_grams


@dataclass(frozen=True, slots=True)
class ActualWeight:
    ticket_id: str
    piece_id: str
    actual_g: Fraction


def _actual_from_row(row: sqlite3.Row | None) -> ActualWeight | None:
    if row is None:
        return None
    return ActualWeight(row['ticket_id'], row['piece_id'], decode_grams(row['actual_g']))


def read_ticket(db: sqlite3.Connection, ticket_id: str) -> ActualWeight | None:
    row = db.execute('SELECT * FROM actual_weights WHERE ticket_id = ?', (ticket_id,)).fetchone()
    return _actual_from_row(row)


def read_actual(db: sqlite3.Connection, piece_id: str) -> ActualWeight | None:
    row = db.execute('SELECT * FROM actual_weights WHERE piece_id = ?', (piece_id,)).fetchone()
    return _actual_from_row(row)
