import sqlite3

from admission.history import find_admission
from admission.predictions import AdmissionReceipt, make_prediction
from admission.requests import normalize_admission
from station import Boundary, StationPath, ensure_table
from training.models import current_cut
from training.store import training_write
from weighing.units import GramInput, encode_grams


_ROSTER = '''CREATE TABLE admissions (
    piece_id TEXT NOT NULL PRIMARY KEY,
    estimate_g TEXT NOT NULL,
    cut_id INTEGER NOT NULL REFERENCES model_cuts (cut_id),
    compensation_g TEXT NOT NULL,
    prediction_g TEXT NOT NULL
)'''


def initialize_roster(db: sqlite3.Connection) -> None:
    ensure_table(db, 'admissions', _ROSTER)


def admit(
    training_path: StationPath,
    piece_id: str,
    estimate_g: GramInput,
    *,
    on_boundary: Boundary | None = None,
) -> AdmissionReceipt:
    request = normalize_admission(piece_id, estimate_g)
    with training_write(training_path) as db:
        existing = find_admission(db, request.piece_id)
        if existing is not None:
            if existing.estimate_g != request.estimate_g:
                raise ValueError(f'conflicting admission: {request.piece_id}')
            return existing

        receipt = make_prediction(request, current_cut(db))
        db.execute(
            '''INSERT INTO admissions (piece_id, estimate_g, cut_id, compensation_g, prediction_g)
               VALUES (?, ?, ?, ?, ?)''',
            (
                receipt.piece_id, encode_grams(receipt.estimate_g), receipt.cut_id,
                encode_grams(receipt.compensation_g), encode_grams(receipt.prediction_g),
            ),
        )
    if on_boundary is not None:
        on_boundary('after_admission_commit')
    return receipt
