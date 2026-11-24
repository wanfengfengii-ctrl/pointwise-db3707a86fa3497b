from dataclasses import dataclass
from fractions import Fraction

from identifiers import require_identifier
from station import StationFormatError, StationPath
from training.history import load_cut
from training.ledger import FeedbackRecord, append_feedback, find_feedback
from training.models import ModelCut, current_cut, publish_cut
from training.residuals import training_residual
from training.store import training_write
from weighing.units import GramInput, parse_grams


@dataclass(frozen=True, slots=True)
class FeedbackRequest:
    feedback_id: str
    estimate_g: Fraction
    actual_g: Fraction


def feedback(
    training_path: StationPath,
    feedback_id: str,
    estimate_g: GramInput,
    actual_g: GramInput,
) -> ModelCut:
    request = FeedbackRequest(
        require_identifier(feedback_id, 'feedback_id'),
        parse_grams(estimate_g),
        parse_grams(actual_g),
    )
    with training_write(training_path) as db:
        existing = find_feedback(db, request.feedback_id)
        if existing is not None:
            if (existing.estimate_g, existing.actual_g) != (request.estimate_g, request.actual_g):
                raise ValueError(f'conflicting feedback: {request.feedback_id}')
            original = load_cut(db, existing.cut_id)
            if original is None:
                raise StationFormatError('training feedback refers to a missing model cut')
            return original

        residual = training_residual(request.estimate_g, request.actual_g)
        cut = publish_cut(db, current_cut(db), residual)
        append_feedback(
            db, FeedbackRecord(request.feedback_id, request.estimate_g, request.actual_g, cut.cut_id)
        )
        return cut
