import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass

from analysis.eligibility import MissingEvidence, ScoringEvidence, collect_evidence
from analysis.errors import ErrorSummary, compute_errors
from analysis.requests import normalize_report_request
from analysis.retrieval import FrozenReport, PublicationReceipt, read_frozen
from analysis.trials import (
    TrialOperation,
    TrialTicket,
    find_operation,
    find_operation_for_ticket,
    find_trial,
    find_trial_for_piece,
    insert_operation,
    insert_trial,
    mark_validated,
    settle_operation,
    ticket_in_reports,
)
from delivery.store import find_receipt, open_scoring_store
from identifiers import require_identifier
from station import Boundary, StationFormatError, StationPath, ensure_table, transaction
from trial_messages import (
    KIND_QUERY,
    KIND_TRAIN,
    KIND_WITHDRAW,
    STATE_TRAINED,
    STATE_UNKNOWN,
    build_request,
    check_reply,
)
from weighing.history import read_ticket
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

        for pair in evidence:
            if find_trial(db, pair.actual.ticket_id) is not None:
                raise ValueError(
                    f'scale ticket is registered as a trial ticket: {pair.actual.ticket_id}'
                )
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


_PENDING = 'pending'


def _status_dict(db: sqlite3.Connection, trial: TrialTicket) -> dict:
    operation = find_operation_for_ticket(db, trial.ticket_id)
    receipt_no = None
    if trial.report_id is not None:
        row = db.execute(
            'SELECT receipt_no FROM publication_receipts WHERE report_id = ?', (trial.report_id,)
        ).fetchone()
        if row is None:
            raise StationFormatError(f'validated trial ticket has no success receipt: {trial.ticket_id}')
        receipt_no = row['receipt_no']
    if trial.report_id is not None:
        state = 'validated'
    elif operation is not None and operation.transfer_state == STATE_TRAINED:
        state = 'trained'
    elif operation is not None and operation.transfer_state == _PENDING:
        state = 'pending'
    else:
        state = 'eligible'
    return {
        'piece_id': trial.piece_id,
        'ticket_id': trial.ticket_id,
        'state': state,
        'operation_id': operation.operation_id if operation is not None else None,
        'transfer_state': operation.transfer_state if operation is not None else None,
        'report_id': trial.report_id,
        'receipt_no': receipt_no,
        'cut_id': operation.cut_id if operation is not None else None,
    }


def _trial_status_row(db: sqlite3.Connection, ticket_id: str) -> dict:
    trial = find_trial(db, ticket_id)
    if trial is None:
        raise ValueError(f'unknown trial ticket: {ticket_id}')
    return _status_dict(db, trial)


def _read_status(scoring_path: StationPath, ticket_id: str) -> dict:
    with open_scoring_store(scoring_path) as db, transaction(db):
        return _trial_status_row(db, ticket_id)


def _request_for(db: sqlite3.Connection, operation: TrialOperation, kind: str) -> dict:
    """Build the exchange request from the operation's fixed ticket evidence."""
    trial = find_trial(db, operation.ticket_id)
    if trial is None:
        raise StationFormatError('trial operation refers to a missing trial ticket')
    receipt = find_receipt(db, trial.piece_id)
    if receipt is None or receipt.receipt_no != trial.prediction_receipt_no:
        raise StationFormatError('trial ticket refers to a missing prediction receipt')
    actual = read_ticket(db, trial.ticket_id)
    if actual is None or actual.piece_id != trial.piece_id:
        raise StationFormatError('trial ticket refers to a missing scale ticket')
    return build_request(
        kind, operation.operation_id, trial.ticket_id, trial.piece_id,
        receipt.admission.estimate_g, actual.actual_g,
    )


def _apply_reply(
    scoring_path: StationPath, request: dict, reply: object, *, allow_unknown: bool
) -> None:
    """Record a validated exchange reply as the operation's settled state."""
    transfer_state, cut_id = check_reply(request, reply, allow_unknown=allow_unknown)
    if transfer_state == STATE_UNKNOWN:
        return
    with open_scoring_store(scoring_path) as db, transaction(db, write=True):
        operation = find_operation(db, request['operation_id'])
        if operation is None or operation.ticket_id != request['ticket_id']:
            raise ValueError('trial exchange reply refers to an unknown operation')
        if operation.transfer_state != _PENDING:
            return
        settle_operation(db, operation.operation_id, transfer_state, cut_id)


def _resolve_pending(scoring_path: StationPath, ticket_id: str, exchange) -> dict:
    """Read the known status, verifying a pending operation when a call is given."""
    status = _read_status(scoring_path, ticket_id)
    if exchange is None or status['transfer_state'] != _PENDING:
        return status
    with open_scoring_store(scoring_path) as db, transaction(db):
        operation = find_operation(db, status['operation_id'])
        request = _request_for(db, operation, KIND_QUERY)
    try:
        reply = exchange(request)
    except ConnectionError:
        return status
    _apply_reply(scoring_path, request, reply, allow_unknown=True)
    return _read_status(scoring_path, ticket_id)


def _transfer_call(
    scoring_path: StationPath,
    ticket_id: str,
    operation_id: str,
    exchange,
    kind: str,
) -> dict:
    """Send one transfer message for the ticket's pending operation and settle it."""
    if not callable(exchange):
        raise ValueError('a callable exchange is required to reach the training station')
    with open_scoring_store(scoring_path) as db, transaction(db):
        operation = find_operation(db, operation_id)
        request = _request_for(db, operation, kind)
    try:
        reply = exchange(request)
    except ConnectionError:
        return _read_status(scoring_path, ticket_id)
    _apply_reply(scoring_path, request, reply, allow_unknown=False)
    return _read_status(scoring_path, ticket_id)


def register_trial(
    scoring_path: StationPath, piece_id: str, ticket_id: str, exchange
) -> dict:
    """Register a received prediction and its same-piece scale ticket as a trial ticket."""
    piece_id = require_identifier(piece_id, 'piece_id')
    ticket_id = require_identifier(ticket_id, 'ticket_id')
    with open_scoring_store(scoring_path) as db, transaction(db, write=True):
        trial = find_trial(db, ticket_id)
        if trial is not None:
            if trial.piece_id != piece_id:
                raise ValueError(f'trial ticket belongs to another piece: {ticket_id}')
        else:
            if find_trial_for_piece(db, piece_id) is not None:
                raise ValueError(f'piece already has a registered trial ticket: {piece_id}')
            receipt = find_receipt(db, piece_id)
            if receipt is None:
                raise ValueError(f'no received original prediction for piece: {piece_id}')
            actual = read_ticket(db, ticket_id)
            if actual is None or actual.piece_id != piece_id:
                raise ValueError(f'scale ticket does not belong to the piece: {ticket_id}')
            if ticket_in_reports(db, ticket_id):
                raise ValueError(f'ticket already counted in a normal report: {ticket_id}')
            insert_trial(db, TrialTicket(ticket_id, piece_id, receipt.receipt_no, None))
    return _resolve_pending(scoring_path, ticket_id, exchange)


def request_training(
    scoring_path: StationPath, ticket_id: str, operation_id: str, exchange
) -> dict:
    """Apply once for training conversion under an operation id, or redeliver it."""
    ticket_id = require_identifier(ticket_id, 'ticket_id')
    operation_id = require_identifier(operation_id, 'operation_id')
    with open_scoring_store(scoring_path) as db, transaction(db, write=True):
        trial = find_trial(db, ticket_id)
        if trial is None:
            raise ValueError(f'unknown trial ticket: {ticket_id}')
        operation = find_operation_for_ticket(db, ticket_id)
        if operation is None:
            if trial.report_id is not None:
                raise ValueError(f'validated trial ticket cannot apply for training: {ticket_id}')
            if find_operation(db, operation_id) is not None:
                raise ValueError(f'operation id is bound to another trial ticket: {operation_id}')
            if not callable(exchange):
                raise ValueError('a callable exchange is required to apply for training')
            insert_operation(db, TrialOperation(operation_id, ticket_id, _PENDING, None))
        elif operation.operation_id != operation_id:
            raise ValueError(
                f'trial ticket already applied under operation: {operation.operation_id}'
            )
        status = _status_dict(db, trial)
    if status['transfer_state'] != _PENDING:
        return status
    return _transfer_call(scoring_path, ticket_id, operation_id, exchange, KIND_TRAIN)


def withdraw_training(
    scoring_path: StationPath, ticket_id: str, operation_id: str, exchange
) -> dict:
    """Withdraw the ticket's already applied operation, or claim its final state."""
    ticket_id = require_identifier(ticket_id, 'ticket_id')
    operation_id = require_identifier(operation_id, 'operation_id')
    with open_scoring_store(scoring_path) as db, transaction(db):
        trial = find_trial(db, ticket_id)
        if trial is None:
            raise ValueError(f'unknown trial ticket: {ticket_id}')
        operation = find_operation_for_ticket(db, ticket_id)
        if operation is None:
            raise ValueError(f'no applied training operation to withdraw: {ticket_id}')
        if operation.operation_id != operation_id:
            raise ValueError(f'trial ticket operation mismatch: {operation_id}')
        status = _status_dict(db, trial)
    if status['transfer_state'] != _PENDING:
        return status
    return _transfer_call(scoring_path, ticket_id, operation_id, exchange, KIND_WITHDRAW)


def trial_status(scoring_path: StationPath, ticket_id: str, exchange=None) -> dict:
    """Read the known trial status; with a call, verify the pending operation."""
    ticket_id = require_identifier(ticket_id, 'ticket_id')
    with open_scoring_store(scoring_path) as db, transaction(db):
        _trial_status_row(db, ticket_id)
    return _resolve_pending(scoring_path, ticket_id, exchange)


def sign_trial(
    scoring_path: StationPath,
    ticket_id: str,
    report_id: str,
    *,
    on_boundary: Boundary | None = None,
) -> FrozenReport:
    """Sign the single-piece validation report for an eligible trial ticket."""
    ticket_id = require_identifier(ticket_id, 'ticket_id')
    report_id = require_identifier(report_id, 'report_id')
    with open_scoring_store(scoring_path) as db, transaction(db, write=True):
        trial = find_trial(db, ticket_id)
        if trial is None:
            raise ValueError(f'unknown trial ticket: {ticket_id}')
        operation = find_operation_for_ticket(db, ticket_id)
        if operation is not None and operation.transfer_state == _PENDING:
            raise ValueError(f'training transfer is still unconfirmed: {ticket_id}')
        if operation is not None and operation.transfer_state == STATE_TRAINED:
            raise ValueError(f'trial ticket is already consumed by training: {ticket_id}')
        if trial.report_id is not None:
            if trial.report_id != report_id:
                raise ValueError(
                    f'trial ticket already validated under report: {trial.report_id}'
                )
            frozen = read_frozen(db, report_id)
            if frozen is None:
                raise StationFormatError(f'validated trial ticket is missing its report: {report_id}')
            return frozen
        if read_frozen(db, report_id) is not None:
            raise ValueError(f'report id already signed for another request: {report_id}')
        receipt = find_receipt(db, trial.piece_id)
        if receipt is None or receipt.receipt_no != trial.prediction_receipt_no:
            raise StationFormatError(f'trial ticket refers to a missing prediction receipt: {ticket_id}')
        actual = read_ticket(db, ticket_id)
        if actual is None or actual.piece_id != trial.piece_id:
            raise StationFormatError(f'trial ticket refers to a missing scale ticket: {ticket_id}')
        summary = compute_errors((ScoringEvidence(receipt, actual),))
        _persist_report(db, report_id, summary)
        cursor = db.execute(
            'INSERT INTO publication_receipts (report_id) VALUES (?)', (report_id,)
        )
        receipt_out = PublicationReceipt(report_id, cursor.lastrowid)
        mark_validated(db, ticket_id, report_id)
        if on_boundary is not None:
            on_boundary('before_report_commit')
        return FrozenReport(report_id, summary, receipt_out)
