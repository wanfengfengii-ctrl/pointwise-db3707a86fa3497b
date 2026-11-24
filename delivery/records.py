import sqlite3
from dataclasses import dataclass

from admission.history import find_admission
from admission.predictions import ADMISSION_PREFIX, AdmissionReceipt
from identifiers import require_identifier
from station import StationFormatError, StationPath, ensure_table
from training.store import training_write


_DELIVERY_PREFIX = 'prediction/'
_DISPATCH = '''CREATE TABLE dispatches (
    delivery_id TEXT NOT NULL PRIMARY KEY,
    piece_id TEXT NOT NULL UNIQUE REFERENCES admissions (piece_id)
)'''


@dataclass(frozen=True, slots=True)
class DeliveryRecord:
    delivery_id: str
    admission: AdmissionReceipt


def delivery_id_for(admission: AdmissionReceipt) -> str:
    return _DELIVERY_PREFIX + admission.admission_id


def _piece_for(delivery_id: str) -> str:
    delivery_id = require_identifier(delivery_id, 'delivery_id')
    prefix = _DELIVERY_PREFIX + ADMISSION_PREFIX
    if not delivery_id.startswith(prefix):
        raise ValueError(f'invalid delivery identity: {delivery_id}')
    return require_identifier(delivery_id[len(prefix):], 'piece_id')


def initialize_dispatch(db: sqlite3.Connection) -> None:
    ensure_table(db, 'dispatches', _DISPATCH)


def read_delivery(db: sqlite3.Connection, delivery_id: str) -> DeliveryRecord | None:
    piece_id = _piece_for(delivery_id)
    row = db.execute('SELECT piece_id FROM dispatches WHERE delivery_id = ?', (delivery_id,)).fetchone()
    if row is None:
        return None
    if row['piece_id'] != piece_id:
        raise StationFormatError('staged delivery has a conflicting piece identity')
    return load_original_delivery(db, delivery_id)


def load_original_delivery(db: sqlite3.Connection, delivery_id: str) -> DeliveryRecord:
    piece_id = _piece_for(delivery_id)
    admission = find_admission(db, piece_id)
    if admission is None:
        raise ValueError(f'no committed admission for delivery: {delivery_id}')
    return DeliveryRecord(delivery_id, admission)


def stage_delivery(training_path: StationPath, source: AdmissionReceipt | str) -> DeliveryRecord:
    delivery_id = delivery_id_for(source) if isinstance(source, AdmissionReceipt) else source
    with training_write(training_path) as db:
        staged = read_delivery(db, delivery_id)
        record = staged if staged is not None else load_original_delivery(db, delivery_id)
        if isinstance(source, AdmissionReceipt) and source != record.admission:
            raise ValueError(f'delivery does not match the committed admission: {delivery_id}')
        if staged is None:
            db.execute(
                'INSERT INTO dispatches (delivery_id, piece_id) VALUES (?, ?)',
                (record.delivery_id, record.admission.piece_id),
            )
        return record
