import sqlite3
from dataclasses import dataclass
from typing import Literal

from delivery.intake import require_same_delivery
from delivery.records import load_original_delivery, read_delivery
from delivery.store import ReceivedPrediction, find_receipt, open_scoring_store
from station import StationPath, temporarily_unavailable, transaction
from training.store import open_training_store


@dataclass(frozen=True, slots=True)
class DeliveryStatus:
    delivery_id: str
    receipt: ReceivedPrediction | None = None

    @property
    def state(self) -> Literal['pending', 'received']:
        return 'pending' if self.receipt is None else 'received'


def delivery_status(training_path: StationPath, scoring_path: StationPath, delivery_id: str) -> DeliveryStatus:
    with open_training_store(training_path) as source, transaction(source):
        staged = read_delivery(source, delivery_id)
        record = staged if staged is not None else load_original_delivery(source, delivery_id)
    try:
        with open_scoring_store(scoring_path) as scoring:
            receipt = find_receipt(scoring, record.admission.piece_id)
    except sqlite3.OperationalError as error:
        if not temporarily_unavailable(error):
            raise
        return DeliveryStatus(delivery_id)
    if receipt is not None:
        require_same_delivery(receipt, record)
    return DeliveryStatus(delivery_id, receipt)
