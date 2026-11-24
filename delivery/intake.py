from fractions import Fraction

from delivery.records import DeliveryRecord, delivery_id_for
from delivery.store import ReceivedPrediction, find_receipt, open_scoring_store, persist_receipt
from identifiers import require_identifier
from station import Boundary, StationPath, transaction


def _validate_delivery(record: DeliveryRecord) -> None:
    admission = record.admission
    require_identifier(admission.piece_id, 'piece_id')
    if record.delivery_id != delivery_id_for(admission):
        raise ValueError('delivery identity does not match the admission')
    if type(admission.cut_id) is not int or not 0 <= admission.cut_id < (1 << 63):
        raise ValueError('invalid training cut in prediction delivery')
    quantities = (admission.estimate_g, admission.compensation_g, admission.prediction_g)
    if any(not isinstance(quantity, Fraction) for quantity in quantities):
        raise ValueError('prediction delivery requires exact quantities')
    if admission.estimate_g < 0 or (admission.cut_id == 0 and admission.compensation_g != 0):
        raise ValueError('invalid original estimate or zero-cut compensation')
    if admission.prediction_g != admission.estimate_g + admission.compensation_g:
        raise ValueError('prediction does not match its estimate and training compensation')


def require_same_delivery(receipt: ReceivedPrediction, record: DeliveryRecord) -> None:
    if receipt.delivery_id != record.delivery_id or receipt.admission != record.admission:
        raise ValueError(f'conflicting prediction for delivery: {record.delivery_id}')


def accept_delivery(
    scoring_path: StationPath,
    record: DeliveryRecord,
    *,
    on_boundary: Boundary | None = None,
) -> ReceivedPrediction:
    _validate_delivery(record)
    with open_scoring_store(scoring_path) as db, transaction(db, write=True):
        existing = find_receipt(db, record.admission.piece_id)
        if existing is not None:
            require_same_delivery(existing, record)
            return existing
        receipt = persist_receipt(db, record)
        if on_boundary is not None:
            on_boundary('before_receipt_commit')
    if on_boundary is not None:
        on_boundary('after_receipt_commit')
    return receipt
