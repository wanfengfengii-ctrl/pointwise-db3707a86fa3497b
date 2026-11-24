import sqlite3
from dataclasses import dataclass
from fractions import Fraction

from station import ensure_table
from trial_messages import STATE_CANCELLED, STATE_TRAINED, TrialMessage
from weighing.units import decode_grams, encode_grams


_TRANSFERS = '''CREATE TABLE trial_transfers (
    operation_id TEXT NOT NULL PRIMARY KEY,
    ticket_id TEXT NOT NULL,
    piece_id TEXT NOT NULL,
    estimate_g TEXT NOT NULL,
    actual_g TEXT NOT NULL,
    transfer_state TEXT NOT NULL CHECK (transfer_state IN ('cancelled', 'trained')),
    cut_id INTEGER UNIQUE REFERENCES model_cuts (cut_id) CHECK (cut_id IS NULL OR cut_id > 0),
    CHECK ((transfer_state = 'trained') = (cut_id IS NOT NULL))
)'''


@dataclass(frozen=True, slots=True)
class TrialTransfer:
    """The training station's committed formal result for one operation."""

    operation_id: str
    ticket_id: str
    piece_id: str
    estimate_g: Fraction
    actual_g: Fraction
    transfer_state: str
    cut_id: int | None

    def matches(self, request: TrialMessage) -> bool:
        return (
            self.ticket_id == request.ticket_id
            and self.piece_id == request.piece_id
            and self.estimate_g == request.estimate_g
            and self.actual_g == request.actual_g
        )


def initialize_transfers(db: sqlite3.Connection) -> None:
    ensure_table(db, 'trial_transfers', _TRANSFERS)


def find_transfer(db: sqlite3.Connection, operation_id: str) -> TrialTransfer | None:
    row = db.execute(
        'SELECT * FROM trial_transfers WHERE operation_id = ?', (operation_id,)
    ).fetchone()
    if row is None:
        return None
    return TrialTransfer(
        operation_id=row['operation_id'],
        ticket_id=row['ticket_id'],
        piece_id=row['piece_id'],
        estimate_g=decode_grams(row['estimate_g']),
        actual_g=decode_grams(row['actual_g']),
        transfer_state=row['transfer_state'],
        cut_id=row['cut_id'],
    )


def append_transfer(db: sqlite3.Connection, record: TrialTransfer) -> None:
    if record.transfer_state not in (STATE_TRAINED, STATE_CANCELLED):
        raise ValueError(f'unknown trial transfer state: {record.transfer_state!r}')
    db.execute(
        '''INSERT INTO trial_transfers (
               operation_id, ticket_id, piece_id, estimate_g, actual_g, transfer_state, cut_id
           ) VALUES (?, ?, ?, ?, ?, ?, ?)''',
        (
            record.operation_id, record.ticket_id, record.piece_id,
            encode_grams(record.estimate_g), encode_grams(record.actual_g),
            record.transfer_state, record.cut_id,
        ),
    )
