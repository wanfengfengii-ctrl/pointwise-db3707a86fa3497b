import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from station import TRAINING_SITE, StationPath, open_station, transaction


@contextmanager
def open_training_store(path: StationPath) -> Iterator[sqlite3.Connection]:
    with open_station(path, TRAINING_SITE) as db:
        yield db


@contextmanager
def training_write(path: StationPath) -> Iterator[sqlite3.Connection]:
    with open_training_store(path) as db, transaction(db, write=True):
        yield db
