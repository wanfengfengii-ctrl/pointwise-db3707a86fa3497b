import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass

from analysis.eligibility import MissingEvidence, collect_evidence
from analysis.errors import ErrorSummary, compute_errors
from analysis.requests import normalize_report_request
from analysis.retrieval import FrozenReport, PublicationReceipt, read_frozen
from delivery.store import open_scoring_store
from station import Boundary, StationPath, ensure_table, transaction
from weighing.units import encode_grams


_REPORTS = '''CREATE TABLE reports (
    report_id TEXT NOT NULL PRIMARY KEY,
    row_count INTEGER NOT NULL CHECK (row_count > 0),
    mae_g TEXT NOT NULL
)'''
_REPORT_ROWS = '''CREATE TABLE report_rows (
    report_id TEXT NOT NULL REFERENCES reports (report_id),
    position INTEGER NOT NULL CHECK (position >= 0),
    piece_id TEXT NOT NULL,
    delivery_id TEXT NOT NULL,
    prediction_receipt_no INTEGER NOT NULL,
    cut_id INTEGER NOT NULL,
    prediction_g TEXT NOT NULL,
    ticket_id TEXT NOT NULL,
    actual_g TEXT NOT NULL,
    signed_error_g TEXT NOT NULL,
    absolute_error_g TEXT NOT NULL,
    PRIMARY KEY (report_id, position)
)'''
_PUBLICATION_RECEIPTS = '''CREATE TABLE publication_receipts (
    receipt_no INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id TEXT NOT NULL UNIQUE REFERENCES reports (report_id)
)'''


@dataclass(frozen=True, slots=True)
class PendingReport:
    report_id: str
    missing: tuple[MissingEvidence, ...]


def initialize_reports(db: sqlite3.Connection) -> None:
    ensure_table(db, 'reports', _REPORTS)
    ensure_table(db, 'report_rows', _REPORT_ROWS)
    ensure_table(db, 'publication_receipts', _PUBLICATION_RECEIPTS)


def _persist_report(db: sqlite3.Connection, report_id: str, summary: ErrorSummary) -> None:
    db.execute(
        'INSERT INTO reports (report_id, row_count, mae_g) VALUES (?, ?, ?)',
        (report_id, len(summary.rows), encode_grams(summary.mae_g)),
    )
    db.executemany(
        '''INSERT INTO report_rows (
               report_id, position, piece_id, delivery_id, prediction_receipt_no, cut_id,
               prediction_g, ticket_id, actual_g, signed_error_g, absolute_error_g
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (
            (
                report_id, position, row.piece_id, row.delivery_id, row.prediction_receipt_no,
                row.cut_id, encode_grams(row.prediction_g), row.ticket_id, encode_grams(row.actual_g),
                encode_grams(row.signed_error_g), encode_grams(row.absolute_error_g),
            )
            for position, row in enumerate(summary.rows)
        ),
    )


def report(
    scoring_path: StationPath,
    report_id: str,
    piece_ids: Sequence[str],
    *,
    on_boundary: Boundary | None = None,
) -> FrozenReport | PendingReport:
    request = normalize_report_request(report_id, piece_ids)
    with open_scoring_store(scoring_path) as db, transaction(db, write=True):
        existing = read_frozen(db, request.report_id)
        if existing is not None:
            if existing.piece_ids != request.piece_ids:
                raise ValueError(f'conflicting report request: {request.report_id}')
            return existing

        evidence, missing = collect_evidence(db, request)
        if missing:
            return PendingReport(request.report_id, missing)

        summary = compute_errors(evidence)
        _persist_report(db, request.report_id, summary)
        if on_boundary is not None:
            on_boundary('after_report_write')
        cursor = db.execute(
            'INSERT INTO publication_receipts (report_id) VALUES (?)', (request.report_id,)
        )
        receipt = PublicationReceipt(request.report_id, cursor.lastrowid)
        if on_boundary is not None:
            on_boundary('before_report_commit')
        return FrozenReport(request.report_id, summary, receipt)
