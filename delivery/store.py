import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from admission.predictions import AdmissionReceipt
from delivery.records import DeliveryRecord, delivery_id_for
from station import SCORING_SITE, StationFormatError, StationPath, ensure_table, open_station
from weighing.units import decode_grams, encode_grams


_RECEIPTS = '''CREATE TABLE prediction_receipts (
    receipt_no INTEGER PRIMARY KEY AUTOINCREMENT,
    delivery_id TEXT NOT NULL UNIQUE,
    admission_id TEXT NOT NULL UNIQUE,
    piece_id TEXT NOT NULL UNIQUE,
    estimate_g TEXT NOT NULL,
    cut_id INTEGER NOT NULL CHECK (cut_id >= 0),
    compensation_g TEXT NOT NULL,
    prediction_g TEXT NOT NULL
)'''


@dataclass(frozen=True, slots=True)
class ReceivedPrediction:
    receipt_no: int
    delivery_id: str
    admission: AdmissionReceipt


@contextmanager
def open_scoring_store(path: StationPath) -> Iterator[sqlite3.Connection]:
    with open_station(path, SCORING_SITE) as db:
        yield db


def initialize_receipts(db: sqlite3.Connection) -> None:
    ensure_table(db, 'prediction_receipts', _RECEIPTS)


def find_receipt(db: sqlite3.Connection, piece_id: str) -> ReceivedPrediction | None:
    row = db.execute('SELECT * FROM prediction_receipts WHERE piece_id = ?', (piece_id,)).fetchone()
    if row is None:
        return None
    admission = AdmissionReceipt(
        row['piece_id'], decode_grams(row['estimate_g']), row['cut_id'],
        decode_grams(row['compensation_g']), decode_grams(row['prediction_g']),
    )
    if row['admission_id'] != admission.admission_id or row['delivery_id'] != delivery_id_for(admission):
        raise StationFormatError('stored prediction receipt has a conflicting identity')
    return ReceivedPrediction(row['receipt_no'], row['delivery_id'], admission)


def persist_receipt(db: sqlite3.Connection, record: DeliveryRecord) -> ReceivedPrediction:
    admission = record.admission
    cursor = db.execute(
        '''INSERT INTO prediction_receipts (
               delivery_id, admission_id, piece_id, estimate_g, cut_id, compensation_g, prediction_g
           ) VALUES (?, ?, ?, ?, ?, ?, ?)''',
        (
            record.delivery_id, admission.admission_id, admission.piece_id,
            encode_grams(admission.estimate_g), admission.cut_id,
            encode_grams(admission.compensation_g), encode_grams(admission.prediction_g),
        ),
    )
    return ReceivedPrediction(cursor.lastrowid, record.delivery_id, admission)
