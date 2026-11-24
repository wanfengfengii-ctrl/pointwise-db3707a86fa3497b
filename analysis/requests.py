from collections.abc import Sequence
from dataclasses import dataclass

from identifiers import require_identifier


@dataclass(frozen=True, slots=True)
class ReportRequest:
    report_id: str
    piece_ids: tuple[str, ...]


def normalize_report_request(report_id: str, piece_ids: Sequence[str]) -> ReportRequest:
    report_id = require_identifier(report_id, 'report_id')
    if not isinstance(piece_ids, Sequence) or isinstance(piece_ids, (str, bytes, bytearray)):
        raise ValueError('report requires an ordered sequence of piece identifiers')
    ordered = tuple(require_identifier(piece_id, 'piece_id') for piece_id in piece_ids)
    if not ordered:
        raise ValueError('report requires at least one piece')
    return ReportRequest(report_id, ordered)
