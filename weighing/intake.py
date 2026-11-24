import sqlite3

from delivery.store import open_scoring_store
from station import StationPath, ensure_table, transaction
from weighing.history import ActualWeight, read_actual, read_ticket
from weighing.tickets import normalize_ticket
from weighing.units import GramInput, encode_grams


_ACTUALS = '''CREATE TABLE actual_weights (
    ticket_id TEXT NOT NULL PRIMARY KEY,
    piece_id TEXT NOT NULL UNIQUE,
    actual_g TEXT NOT NULL
)'''


def initialize_actuals(db: sqlite3.Connection) -> None:
    ensure_table(db, 'actual_weights', _ACTUALS)


def weigh(scoring_path: StationPath, ticket_id: str, piece_id: str, actual_g: GramInput) -> ActualWeight:
    ticket = normalize_ticket(ticket_id, piece_id, actual_g)
    actual = ActualWeight(ticket.ticket_id, ticket.piece_id, ticket.actual_g)
    with open_scoring_store(scoring_path) as db, transaction(db, write=True):
        existing = read_ticket(db, ticket.ticket_id)
        if existing is not None:
            if existing != actual:
                raise ValueError(f'conflicting scale ticket: {ticket.ticket_id}')
            return existing
        if read_actual(db, ticket.piece_id) is not None:
            raise ValueError(f'piece already has a scale ticket: {ticket.piece_id}')
        db.execute(
            'INSERT INTO actual_weights (ticket_id, piece_id, actual_g) VALUES (?, ?, ?)',
            (ticket.ticket_id, ticket.piece_id, encode_grams(ticket.actual_g)),
        )
        return actual
