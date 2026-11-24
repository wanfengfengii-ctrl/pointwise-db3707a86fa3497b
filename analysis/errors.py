from dataclasses import dataclass
from fractions import Fraction

from analysis.eligibility import ScoringEvidence


@dataclass(frozen=True, slots=True)
class ErrorRow:
    piece_id: str
    delivery_id: str
    prediction_receipt_no: int
    cut_id: int
    prediction_g: Fraction
    ticket_id: str
    actual_g: Fraction
    signed_error_g: Fraction
    absolute_error_g: Fraction


@dataclass(frozen=True, slots=True)
class ErrorSummary:
    rows: tuple[ErrorRow, ...]
    mae_g: Fraction


def compute_errors(evidence: tuple[ScoringEvidence, ...]) -> ErrorSummary:
    if not evidence:
        raise ValueError('at least one complete evidence pair is required')
    rows = []
    for pair in evidence:
        receipt = pair.prediction
        admission = receipt.admission
        actual = pair.actual
        if admission.piece_id != actual.piece_id:
            raise ValueError('prediction and scale ticket belong to different pieces')
        error = actual.actual_g - admission.prediction_g
        rows.append(ErrorRow(
            piece_id=admission.piece_id,
            delivery_id=receipt.delivery_id,
            prediction_receipt_no=receipt.receipt_no,
            cut_id=admission.cut_id,
            prediction_g=admission.prediction_g,
            ticket_id=actual.ticket_id,
            actual_g=actual.actual_g,
            signed_error_g=error,
            absolute_error_g=abs(error),
        ))
    total = sum((row.absolute_error_g for row in rows), Fraction(0))
    return ErrorSummary(tuple(rows), total / len(rows))
