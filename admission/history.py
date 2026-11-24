import sqlite3

from admission.predictions import AdmissionReceipt
from identifiers import require_identifier
from station import StationPath
from training.store import open_training_store
from weighing.units import decode_grams


def find_admission(db: sqlite3.Connection, piece_id: str) -> AdmissionReceipt | None:
    row = db.execute('SELECT * FROM admissions WHERE piece_id = ?', (piece_id,)).fetchone()
    if row is None:
        return None
    return AdmissionReceipt(
        piece_id=row['piece_id'],
        estimate_g=decode_grams(row['estimate_g']),
        cut_id=row['cut_id'],
        compensation_g=decode_grams(row['compensation_g']),
        prediction_g=decode_grams(row['prediction_g']),
    )


def read_admission(training_path: StationPath, piece_id: str) -> AdmissionReceipt | None:
    piece_id = require_identifier(piece_id, 'piece_id')
    with open_training_store(training_path) as db:
        return find_admission(db, piece_id)
