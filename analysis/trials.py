import sqlite3
from dataclasses import dataclass

from station import ensure_table


_TRIAL_TICKETS = '''CREATE TABLE trial_tickets (
    ticket_id TEXT NOT NULL PRIMARY KEY,
    piece_id TEXT NOT NULL UNIQUE,
    prediction_receipt_no INTEGER NOT NULL REFERENCES prediction_receipts (receipt_no),
    report_id TEXT REFERENCES reports (report_id)
)'''
_TRIAL_OPERATIONS = '''CREATE TABLE trial_operations (
    operation_id TEXT NOT NULL PRIMARY KEY,
    ticket_id TEXT NOT NULL UNIQUE REFERENCES trial_tickets (ticket_id),
    transfer_state TEXT NOT NULL CHECK (transfer_state IN ('pending', 'cancelled', 'trained')),
    cut_id INTEGER CHECK (cut_id IS NULL OR cut_id > 0),
    CHECK ((transfer_state = 'trained') = (cut_id IS NOT NULL))
)'''


@dataclass(frozen=True, slots=True)
class TrialTicket:
    """A registered trial ticket with its fixed piece and receipt identity."""

    ticket_id: str
    piece_id: str
    prediction_receipt_no: int
    report_id: str | None


@dataclass(frozen=True, slots=True)
class TrialOperation:
    """The scoring station's known state for one applied transfer operation."""

    operation_id: str
    ticket_id: str
    transfer_state: str
    cut_id: int | None


def initialize_trials(db: sqlite3.Connection) -> None:
    ensure_table(db, 'trial_tickets', _TRIAL_TICKETS)
    ensure_table(db, 'trial_operations', _TRIAL_OPERATIONS)


def _trial_from_row(row: sqlite3.Row | None) -> TrialTicket | None:
    if row is None:
        return None
    return TrialTicket(
        ticket_id=row['ticket_id'],
        piece_id=row['piece_id'],
        prediction_receipt_no=row['prediction_receipt_no'],
        report_id=row['report_id'],
    )


def find_trial(db: sqlite3.Connection, ticket_id: str) -> TrialTicket | None:
    row = db.execute('SELECT * FROM trial_tickets WHERE ticket_id = ?', (ticket_id,)).fetchone()
    return _trial_from_row(row)


def find_trial_for_piece(db: sqlite3.Connection, piece_id: str) -> TrialTicket | None:
    row = db.execute('SELECT * FROM trial_tickets WHERE piece_id = ?', (piece_id,)).fetchone()
    return _trial_from_row(row)


def insert_trial(db: sqlite3.Connection, trial: TrialTicket) -> None:
    db.execute(
        '''INSERT INTO trial_tickets (ticket_id, piece_id, prediction_receipt_no, report_id)
           VALUES (?, ?, ?, ?)''',
        (trial.ticket_id, trial.piece_id, trial.prediction_receipt_no, trial.report_id),
    )


def mark_validated(db: sqlite3.Connection, ticket_id: str, report_id: str) -> None:
    db.execute(
        'UPDATE trial_tickets SET report_id = ? WHERE ticket_id = ?', (report_id, ticket_id)
    )


def _operation_from_row(row: sqlite3.Row | None) -> TrialOperation | None:
    if row is None:
        return None
    return TrialOperation(
        operation_id=row['operation_id'],
        ticket_id=row['ticket_id'],
        transfer_state=row['transfer_state'],
        cut_id=row['cut_id'],
    )


def find_operation(db: sqlite3.Connection, operation_id: str) -> TrialOperation | None:
    row = db.execute(
        'SELECT * FROM trial_operations WHERE operation_id = ?', (operation_id,)
    ).fetchone()
    return _operation_from_row(row)


def find_operation_for_ticket(db: sqlite3.Connection, ticket_id: str) -> TrialOperation | None:
    row = db.execute(
        'SELECT * FROM trial_operations WHERE ticket_id = ?', (ticket_id,)
    ).fetchone()
    return _operation_from_row(row)


def insert_operation(db: sqlite3.Connection, operation: TrialOperation) -> None:
    db.execute(
        '''INSERT INTO trial_operations (operation_id, ticket_id, transfer_state, cut_id)
           VALUES (?, ?, ?, ?)''',
        (operation.operation_id, operation.ticket_id, operation.transfer_state, operation.cut_id),
    )


def settle_operation(
    db: sqlite3.Connection, operation_id: str, transfer_state: str, cut_id: int | None
) -> None:
    db.execute(
        'UPDATE trial_operations SET transfer_state = ?, cut_id = ? WHERE operation_id = ?',
        (transfer_state, cut_id, operation_id),
    )


def ticket_in_reports(db: sqlite3.Connection, ticket_id: str) -> bool:
    """Whether the ticket has already been counted into any signed report."""
    return db.execute(
        'SELECT 1 FROM report_rows WHERE ticket_id = ? LIMIT 1', (ticket_id,)
    ).fetchone() is not None
