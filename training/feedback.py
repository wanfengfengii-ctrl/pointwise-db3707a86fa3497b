from dataclasses import dataclass
from fractions import Fraction

from identifiers import require_identifier
from station import Boundary, StationFormatError, StationPath
from training.history import load_cut
from training.ledger import FeedbackRecord, append_feedback, find_feedback
from training.models import ModelCut, current_cut, publish_cut
from training.residuals import training_residual
from training.store import training_write
from training.transfers import TrialTransfer, append_transfer, find_transfer
from trial_messages import (
    KIND_QUERY,
    KIND_TRAIN,
    STATE_CANCELLED,
    STATE_TRAINED,
    STATE_UNKNOWN,
    build_reply,
    parse_request,
)
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


def _transfer_reply(kind: str, record: TrialTransfer) -> dict:
    return build_reply(
        kind, record.operation_id, record.ticket_id, record.piece_id,
        record.transfer_state, record.cut_id,
    )


def exchange_trial(
    training_path: StationPath,
    message: object,
    *,
    on_boundary: Boundary | None = None,
) -> dict:
    """Handle one trial transfer call on the training station only.

    A first-time ``trial_train`` publishes a real new cut on the shared cut
    sequence using the original estimate's residual, and records the formal
    training result under the operation id. A first-time ``trial_withdraw``
    records the withdrawal final state, so a late original training request
    can no longer publish a cut. Replays of an established operation return
    the committed result; conflicting identities or changed parameters are
    rejected. ``trial_query`` only reads and answers ``unknown`` for an
    operation the station has never decided.
    """
    request = parse_request(message)
    with training_write(training_path) as db:
        existing = find_transfer(db, request.operation_id)
        if existing is not None:
            if not existing.matches(request):
                raise ValueError(f'conflicting trial transfer: {request.operation_id}')
            return _transfer_reply(request.kind, existing)
        if request.kind == KIND_QUERY:
            return build_reply(
                request.kind, request.operation_id, request.ticket_id, request.piece_id,
                STATE_UNKNOWN, None,
            )
        if request.kind == KIND_TRAIN:
            residual = training_residual(request.estimate_g, request.actual_g)
            cut = publish_cut(db, current_cut(db), residual)
            record = TrialTransfer(
                request.operation_id, request.ticket_id, request.piece_id,
                request.estimate_g, request.actual_g, STATE_TRAINED, cut.cut_id,
            )
        else:
            record = TrialTransfer(
                request.operation_id, request.ticket_id, request.piece_id,
                request.estimate_g, request.actual_g, STATE_CANCELLED, None,
            )
        append_transfer(db, record)
        if on_boundary is not None:
            on_boundary('before_training_commit')
        return _transfer_reply(request.kind, record)
