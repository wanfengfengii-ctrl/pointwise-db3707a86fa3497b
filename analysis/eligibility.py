import sqlite3
from dataclasses import dataclass

from analysis.requests import ReportRequest
from delivery.store import ReceivedPrediction, find_receipt
from weighing.history import ActualWeight, read_actual


@dataclass(frozen=True, slots=True)
class ScoringEvidence:
    prediction: ReceivedPrediction
    actual: ActualWeight


@dataclass(frozen=True, slots=True)
class MissingEvidence:
    piece_id: str
    prediction_missing: bool
    actual_missing: bool


def collect_evidence(
    db: sqlite3.Connection, request: ReportRequest,
) -> tuple[tuple[ScoringEvidence, ...], tuple[MissingEvidence, ...]]:
    ready = []
    missing = []
    for piece_id in request.piece_ids:
        prediction = find_receipt(db, piece_id)
        actual = read_actual(db, piece_id)
        if prediction is None or actual is None:
            missing.append(MissingEvidence(piece_id, prediction is None, actual is None))
        else:
            ready.append(ScoringEvidence(prediction, actual))
    return tuple(ready), tuple(missing)
