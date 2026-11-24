import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path


StationPath = str | Path
Boundary = Callable[[str], None]
TRAINING_SITE = 0x50575431
SCORING_SITE = 0x50575331
SCHEMA_VERSION = 1


class StationFormatError(RuntimeError):
    """The database is not a compatible station store."""


@contextmanager
def connect_station(path: StationPath, *, create: bool = False) -> Iterator[sqlite3.Connection]:
    mode = 'rwc' if create else 'rw'
    uri = f'{Path(path).resolve().as_uri()}?mode={mode}'
    db = sqlite3.connect(uri, uri=True, isolation_level=None)
    try:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys = ON')
        yield db
    finally:
        db.close()


@contextmanager
def transaction(db: sqlite3.Connection, *, write: bool = False) -> Iterator[sqlite3.Connection]:
    db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise


def _station_header(db: sqlite3.Connection) -> tuple[int, int]:
    application_id = db.execute('PRAGMA application_id').fetchone()[0]
    version = db.execute('PRAGMA user_version').fetchone()[0]
    return application_id, version


@contextmanager
def open_station(path: StationPath, application_id: int) -> Iterator[sqlite3.Connection]:
    with connect_station(path) as db:
        if _station_header(db) != (application_id, SCHEMA_VERSION):
            raise StationFormatError(f'incompatible or uninitialized station store: {path}')
        yield db


def ensure_table(db: sqlite3.Connection, name: str, declaration: str) -> None:
    existing = db.execute(
        'SELECT type, sql FROM sqlite_schema WHERE name = ? COLLATE NOCASE',
        (name,),
    ).fetchone()
    if existing is None:
        db.execute(declaration)
    elif existing['type'] != 'table' or existing['sql'].split() != declaration.split():
        raise StationFormatError(f'incompatible station table: {name}')


def initialize_station(
    db: sqlite3.Connection,
    application_id: int,
    initializers: tuple[Callable[[sqlite3.Connection], None], ...],
) -> None:
    header = _station_header(db)
    if header == (0, 0):
        occupied = db.execute(
            "SELECT 1 FROM sqlite_schema WHERE name NOT GLOB 'sqlite_*' LIMIT 1"
        ).fetchone()
        if occupied is not None:
            raise StationFormatError('unmarked station database already contains objects')
        db.execute(f'PRAGMA application_id = {application_id}')
        db.execute(f'PRAGMA user_version = {SCHEMA_VERSION}')
    elif header != (application_id, SCHEMA_VERSION):
        raise StationFormatError('incompatible station role or schema version')

    for initializer in initializers:
        initializer(db)
    if db.execute('PRAGMA foreign_key_check').fetchone() is not None:
        raise StationFormatError('station records contain broken references')


def temporarily_unavailable(error: sqlite3.OperationalError) -> bool:
    code = getattr(error, 'sqlite_errorcode', 0)
    return code & 0xFF in {
        sqlite3.SQLITE_BUSY,
        sqlite3.SQLITE_LOCKED,
        sqlite3.SQLITE_CANTOPEN,
        sqlite3.SQLITE_IOERR,
    }
