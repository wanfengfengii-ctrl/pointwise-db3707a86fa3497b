import sqlite3
from dataclasses import dataclass
from fractions import Fraction

from station import StationFormatError, ensure_table
from training.residuals import mean_residual
from weighing.units import decode_grams, encode_grams


_MODELS = '''CREATE TABLE model_cuts (
    cut_id INTEGER PRIMARY KEY CHECK (cut_id >= 0),
    residual_total_g TEXT NOT NULL,
    compensation_g TEXT NOT NULL
)'''


@dataclass(frozen=True, slots=True)
class ModelCut:
    cut_id: int
    residual_total_g: Fraction
    compensation_g: Fraction

    @property
    def feedback_count(self) -> int:
        return self.cut_id


def model_from_row(row: sqlite3.Row) -> ModelCut:
    return ModelCut(
        cut_id=row['cut_id'],
        residual_total_g=decode_grams(row['residual_total_g']),
        compensation_g=decode_grams(row['compensation_g']),
    )


def _insert_cut(db: sqlite3.Connection, cut: ModelCut) -> None:
    db.execute(
        'INSERT INTO model_cuts (cut_id, residual_total_g, compensation_g) VALUES (?, ?, ?)',
        (cut.cut_id, encode_grams(cut.residual_total_g), encode_grams(cut.compensation_g)),
    )


def initialize_models(db: sqlite3.Connection) -> None:
    ensure_table(db, 'model_cuts', _MODELS)
    initial = db.execute('SELECT * FROM model_cuts ORDER BY cut_id LIMIT 1').fetchone()
    zero = ModelCut(0, Fraction(0), Fraction(0))
    if initial is None:
        _insert_cut(db, zero)
    elif model_from_row(initial) != zero:
        raise StationFormatError('training station is missing its original zero cut')


def current_cut(db: sqlite3.Connection) -> ModelCut:
    row = db.execute('SELECT * FROM model_cuts ORDER BY cut_id DESC LIMIT 1').fetchone()
    if row is None:
        raise StationFormatError('training station is missing a published cut')
    return model_from_row(row)


def publish_cut(db: sqlite3.Connection, previous: ModelCut, residual_g: Fraction) -> ModelCut:
    cut_id = previous.cut_id + 1
    total_g = previous.residual_total_g + residual_g
    cut = ModelCut(cut_id, total_g, mean_residual(total_g, cut_id))
    _insert_cut(db, cut)
    return cut
