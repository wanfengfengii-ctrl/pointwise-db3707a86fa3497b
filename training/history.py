import sqlite3

from station import StationPath
from training.models import ModelCut, model_from_row
from training.store import open_training_store


def load_cut(db: sqlite3.Connection, cut_id: int) -> ModelCut | None:
    row = db.execute('SELECT * FROM model_cuts WHERE cut_id = ?', (cut_id,)).fetchone()
    return None if row is None else model_from_row(row)


def read_cut(training_path: StationPath, cut_id: int) -> ModelCut | None:
    if type(cut_id) is not int or cut_id < 0:
        raise ValueError('cut_id must be a nonnegative integer')
    if cut_id > (1 << 63) - 1:
        return None
    with open_training_store(training_path) as db:
        return load_cut(db, cut_id)
