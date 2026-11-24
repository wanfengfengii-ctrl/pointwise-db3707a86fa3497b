import sqlite3
from dataclasses import dataclass

from analysis.errors import ErrorRow, ErrorSummary
from delivery.store import open_scoring_store
from identifiers import require_identifier
from station import StationFormatError, StationPath, transaction
from weighing.units import decode_grams


@dataclass(frozen=True, slots=True)
class PublicationReceipt:
    report_id: str
    receipt_no: int


@dataclass(frozen=True, slots=True)
class FrozenReport:
    report_id: str
    summary: ErrorSummary
    receipt: PublicationReceipt

    @property
    def piece_ids(self) -> tuple[str, ...]:
        return tuple(row.piece_id for row in self.summary.rows)


def _error_row(row: sqlite3.Row) -> ErrorRow:
    return ErrorRow(
        piece_id=row['piece_id'],
        delivery_id=row['delivery_id'],
        prediction_receipt_no=row['prediction_receipt_no'],
        cut_id=row['cut_id'],
        prediction_g=decode_grams(row['prediction_g']),
        ticket_id=row['ticket_id'],
        actual_g=decode_grams(row['actual_g']),
        signed_error_g=decode_grams(row['signed_error_g']),
        absolute_error_g=decode_grams(row['absolute_error_g']),
    )


def read_frozen(db: sqlite3.Connection, report_id: str) -> FrozenReport | None:
    header = db.execute(
        '''SELECT reports.row_count, reports.mae_g, publication_receipts.receipt_no
           FROM reports LEFT JOIN publication_receipts USING (report_id)
           WHERE reports.report_id = ?''',
        (report_id,),
    ).fetchone()
    if header is None:
        return None
    if header['receipt_no'] is None:
        raise StationFormatError(f'published report has no success receipt: {report_id}')
    rows = db.execute(
        'SELECT * FROM report_rows WHERE report_id = ? ORDER BY position', (report_id,)
    ).fetchall()
    if len(rows) != header['row_count'] or any(row['position'] != index for index, row in enumerate(rows)):
        raise StationFormatError(f'published report has incomplete detail: {report_id}')
    summary = ErrorSummary(tuple(_error_row(row) for row in rows), decode_grams(header['mae_g']))
    return FrozenReport(report_id, summary, PublicationReceipt(report_id, header['receipt_no']))


def get(scoring_path: StationPath, report_id: str) -> FrozenReport | None:
    report_id = require_identifier(report_id, 'report_id')
    with open_scoring_store(scoring_path) as db, transaction(db):
        return read_frozen(db, report_id)
